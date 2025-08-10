import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import escape_html, get_url, validate_email_address

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# 允许的主机名（Turnstile 返回的 hostname）
ALLOWED_HOSTNAMES = {"athenomics.com", "www.athenomics.com"}


def _get_client_ip() -> str:
	"""尽可能准确获取客户端 IP（兼容代理/Cloudflare）"""
	headers = frappe.request.headers if getattr(frappe, "request", None) else {}
	ip = (
		headers.get("CF-Connecting-IP")
		or headers.get("X-Forwarded-For", "").split(",")[0].strip()
		or getattr(frappe.local, "request_ip", None)
		or "unknown"
	)
	return ip


def _get_turnstile_secret() -> str:
	"""从 Single DocType 读取 Turnstile Secret（保密字段）"""
	api_key = frappe.get_single("NGS API KEY")
	secret = api_key.get_password("turnstile_secret_key")
	if not secret:
		frappe.throw(_("Turnstile secret key not configured in 'NGS API KEY'."))
	return secret


def _verify_turnstile(token: str):
	"""调用 Cloudflare 接口验证 Token，失败抛错"""
	secret = _get_turnstile_secret()
	ip = _get_client_ip()

	try:
		r = requests.post(
			VERIFY_URL,
			data={"secret": secret, "response": token, "remoteip": ip},
			timeout=8,
		)
		r.raise_for_status()
		data = r.json()
	except Exception as e:
		frappe.log_error(f"Turnstile verify request failed: {e}", "Turnstile Verify")
		frappe.throw(_("Human verification failed, please try again later."))

	ok = bool(data.get("success"))

	# 允许多个主机名：athenomics.com 与 www.athenomics.com
	hostname = data.get("hostname")
	host_ok = (hostname in ALLOWED_HOSTNAMES) if hostname else False
	ok = ok and host_ok

	if not ok:
		errs = ", ".join(data.get("error-codes", [])) or "unknown_error"
		frappe.log_error(
			f"Turnstile failed. ip={ip}, hostname={hostname}, errors={errs}, resp={data}", "Turnstile Verify"
		)
		frappe.throw(_("Captcha verification failed. Please try again."))


def _rate_limit(key: str, limit: int, ttl: int):
	"""简单限流：在 ttl 秒内最多 limit 次；用 incr + expire 更原子"""
	cache = frappe.cache()
	try:
		count = cache.incr(key)
		if count == 1:
			cache.expire(key, ttl)
		if count > limit:
			frappe.throw(_("Too many requests, please try later."))
	except Exception:
		# 缓存不可用时，不阻塞主流程
		pass


class NGSContactForm(Document):
	def before_insert(self):
		# ---- 基础清洗/校验 ----
		self.full_name = (self.full_name or "").strip()
		self.email = (self.email or "").strip()
		self.message = (self.message or "").strip()

		if not self.full_name or not self.email or not self.message:
			frappe.throw(_("Full Name, Email and Message are required."))

		email_err = validate_email_address(self.email, throw=False)
		if email_err:
			frappe.throw(_("Invalid email address."))

		if len(self.message) > 8000:
			frappe.throw(_("Message is too long."))

		# ---- Turnstile 校验 ----
		token = (self.get("turnstile_token") or "").strip()
		if not token:
			frappe.throw(_("Captcha token missing."))
		_verify_turnstile(token)
		self.turnstile_token = ""  # 通过后清空

		# ---- 限流（按 IP + 邮箱）----
		ip = _get_client_ip()
		_rate_limit(f"ngs-contact:ip:{ip}", limit=5, ttl=300)  # 5 分钟最多 5 次
		_rate_limit(f"ngs-contact:email:{self.email}", limit=5, ttl=300)

	def after_insert(self):
		"""可选：落库后发通知邮件"""
		try:
			to = frappe.db.get_single_value("Website Settings", "contact_email") or "support@example.com"
			subject = f"New Contact: {self.full_name}"
			message = f"""
                <p><b>Name:</b> {escape_html(self.full_name)}</p>
                <p><b>Email:</b> {escape_html(self.email)}</p>
                <p><b>Message:</b></p>
                <pre>{escape_html(self.message or "")}</pre>
                <p>Record: <a href="{get_url(self.get_url())}">{escape_html(self.name)}</a></p>
            """
			frappe.sendmail(
				recipients=[to],
				subject=subject,
				message=message,
				reference_doctype=self.doctype,
				reference_name=self.name,
			)
		except Exception as e:
			frappe.log_error(f"Send contact notification failed: {e}", "NGS Contact Email")
