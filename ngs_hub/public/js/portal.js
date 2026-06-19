(function () {
	let observer = null;
	let pendingRefresh = false;
	let loginSubmitLookupInstalled = false;

	function markPortalPage() {
		if (!document.body) return;
		document.body.classList.toggle('ngs-portal-body', Boolean(document.querySelector('.ngs-portal')));
	}

	function renameUserMenu() {
		document.querySelectorAll('#website-post-login .dropdown-item[href$="/me"], #website-post-login .dropdown-item[href="/me"]').forEach((link) => {
			if (link.textContent.trim() === 'My Account') {
				link.textContent = 'Profile';
			}
		});
	}

	function getCookie(name) {
		const prefix = `${name}=`;
		const match = document.cookie.split(';').map((item) => item.trim()).find((item) => item.startsWith(prefix));
		return match ? decodeURIComponent(match.slice(prefix.length)) : '';
	}

	function setNgsLoginTarget() {
		if (!window.localStorage) return;
		window.localStorage.setItem('last_visited', '/ngs_home');
	}

	function setLoginTarget(target) {
		if (!target) return;
		if (window.localStorage) {
			window.localStorage.setItem('last_visited', target);
		}
		if (window.location.pathname === '/login') {
			const params = new URLSearchParams(window.location.search);
			params.set('redirect-to', target);
			window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}${window.location.hash}`);
		}
	}

	function clearNgsLoginTarget() {
		if (window.localStorage && window.localStorage.getItem('last_visited') === '/ngs_home') {
			window.localStorage.removeItem('last_visited');
		}
		if (window.location.pathname === '/login') {
			const params = new URLSearchParams(window.location.search);
			const redirectTo = params.get('redirect-to') || '';
			if (redirectTo === '/ngs_home' || redirectTo === '/ngs_account' || redirectTo === '/ngs_register') {
				params.delete('redirect-to');
				const query = params.toString();
				window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`);
			}
		}
	}

	function lookupLoginTarget(email) {
		return fetch(`/api/method/ngs_hub.api.portal.get_login_target?email=${encodeURIComponent(email)}`)
			.then((response) => response.ok ? response.json() : null)
			.then((data) => {
				const target = data && data.message && data.message.target;
				if (target) {
					setLoginTarget(target);
				} else {
					clearNgsLoginTarget();
				}
			})
			.catch(() => {});
	}

	function normalizeLoginDestination() {
		const path = window.location.pathname;
		if (document.querySelector('.ngs-portal')) {
			setNgsLoginTarget();
			return;
		}

		const lastUserWasWebsiteUser = getCookie('system_user') === 'no';
		if (!lastUserWasWebsiteUser) return;

		if (path === '/desk' || path.startsWith('/desk/') || path === '/app' || path.startsWith('/app/')) {
			window.location.replace('/ngs_home');
			return;
		}

		if (path === '/login') {
			const params = new URLSearchParams(window.location.search);
			const redirectTo = params.get('redirect-to') || '';
			if (redirectTo === '/desk' || redirectTo.startsWith('/desk/') || redirectTo === '/app' || redirectTo.startsWith('/app/')) {
				params.set('redirect-to', '/ngs_home');
				window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}${window.location.hash}`);
			}
			setNgsLoginTarget();
		}
	}


	function installNgsSignupLink() {
		if (window.location.pathname !== '/login' || document.querySelector('.ngs-login-signup')) {
			return;
		}
		const loginCard = document.querySelector('.for-login .login-content.page-card');
		if (!loginCard || !loginCard.parentNode) return;
		const message = document.createElement('div');
		message.className = 'text-center sign-up-message ngs-login-signup';
		message.innerHTML = 'No account? <a href="/ngs_register">Sign up</a>';
		loginCard.insertAdjacentElement('afterend', message);
	}

	function installNgsLoginSubmitLookup() {
		if (window.location.pathname !== '/login' || loginSubmitLookupInstalled) {
			return;
		}

		document.addEventListener('submit', (event) => {
			const form = event.target;
			if (!form || !form.matches || !form.matches('.form-login') || form.dataset.ngsTargetChecked === '1') {
				return;
			}

			const email = (form.querySelector('#login_email')?.value || '').trim();
			if (!email) return;

			event.preventDefault();
			event.stopImmediatePropagation();
			lookupLoginTarget(email).finally(() => {
				form.dataset.ngsTargetChecked = '1';
				if (form.requestSubmit) {
					form.requestSubmit();
				} else {
					form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
				}
			});
		}, true);
		loginSubmitLookupInstalled = true;
	}

	function installNgsLoginTargetLookup() {
		if (window.location.pathname !== '/login' || !window.login || !window.login.call || window.login.__ngsTargetLookupInstalled) {
			return;
		}

		const originalCall = window.login.call.bind(window.login);
		window.login.call = function (args, callback, url) {
			if (!args || args.cmd !== 'login' || !args.usr) {
				return originalCall(args, callback, url);
			}

			return lookupLoginTarget(args.usr).then(() => originalCall(args, callback, url));
		};
		window.login.__ngsTargetLookupInstalled = true;
	}

	function refreshPortalChrome() {
		markPortalPage();
		renameUserMenu();
		normalizeLoginDestination();
		installNgsSignupLink();
		installNgsLoginSubmitLookup();
		installNgsLoginTargetLookup();
		if (observer && document.readyState === 'complete') {
			observer.disconnect();
			observer = null;
		}
	}

	function scheduleRefreshPortalChrome() {
		if (pendingRefresh) return;
		pendingRefresh = true;
		window.requestAnimationFrame(() => {
			pendingRefresh = false;
			refreshPortalChrome();
		});
	}

	document.addEventListener('DOMContentLoaded', refreshPortalChrome);
	window.addEventListener('load', refreshPortalChrome);

	observer = new MutationObserver(scheduleRefreshPortalChrome);
	observer.observe(document.documentElement, { childList: true, subtree: true });
})();
