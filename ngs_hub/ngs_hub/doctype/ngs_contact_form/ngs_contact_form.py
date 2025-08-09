import frappe
import requests
from frappe import _
from frappe.model.document import Document

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _get_turnstile_secret():
	# 从 Single DocType 读取 Secret
	api_key = frappe.get_single("NGS API KEY")
	secret = api_key.get_password("turnstile_secret_key")
	if not secret:
		frappe.throw(_("Turnstile secret key not configured in 'NGS API KEY'."))
	return secret


def _verify_turnstile(token: str):
	secret = _get_turnstile_secret()
	r = requests.post(
		VERIFY_URL,
		data={
			"secret": secret,
			"response": token,
			"remoteip": getattr(frappe.local, "request_ip", None),
		},
		timeout=5,
	)
	ok = r.ok and r.json().get("success")
	if not ok:
		frappe.throw(_("Captcha verification failed. Please try again."))


def _rate_limit(key: str, limit: int, ttl: int):
	"""简单限流：在 ttl 秒内最多 limit 次"""
	cache = frappe.cache()
	c = int(cache.get_value(key) or 0) + 1
	cache.set_value(key, c)
	if c == 1:
		cache.expire(key, ttl)
	if c > limit:
		frappe.throw(_("Too many requests, please try later."))


class NGSContactForm(Document):
	# 也可放到 validate()，但放 before_insert 更省一次写库
	def before_insert(self):
		# 1) 必须带 token（来自 Web Form 隐藏字段 turnstile_token）
		token = (self.get("turnstile_token") or "").strip()
		if not token:
			frappe.throw(_("Captcha token missing."))
		# 2) Turnstile 校验
		_verify_turnstile(token)
		# 3) 验完清空，不长期保存
		self.turnstile_token = ""
		# 4) （可选）按 IP 限流：5 分钟最多 5 次
		ip = getattr(frappe.local, "request_ip", "unknown")
		_rate_limit(f"ngs-contact:{ip}", limit=5, ttl=300)

	# 可选：落库后发通知邮件
	def after_insert(self):
		# 如不需要通知，可删除整个方法
		to = frappe.db.get_single_value("Website Settings", "contact_email") or "support@example.com"
		subject = f"New Contact: {self.full_name}"
		message = f"""
            <p><b>Name:</b> {frappe.utils.escape_html(self.full_name)}</p>
            <p><b>Email:</b> {frappe.utils.escape_html(self.email)}</p>
            <p><b>Message:</b></p>
            <pre>{frappe.utils.escape_html(self.message or "")}</pre>
            <p>Record: <a href="{frappe.utils.get_url(self.get_url())}">{self.name}</a></p>
        """
		frappe.sendmail(
			recipients=[to],
			subject=subject,
			message=message,
			reference_doctype=self.doctype,
			reference_name=self.name,
		)
