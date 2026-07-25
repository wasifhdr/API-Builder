"""Anti-bot-detection helpers for replay.

A fresh Playwright/Chromium context is trivially fingerprinted as automated:
`navigator.webdriver === true`, no plugins, a headless UA/WebGL profile, and
the `--enable-automation` switch. Bot-detection scorers (reCAPTCHA v3,
Cloudflare, DataDome) read those tells and challenge the visit — even though a
real Chrome on the same machine, carrying history and a consistent fingerprint,
sails through.

These helpers make replay look like an ordinary human Chrome so the challenge
is never *raised*. They do NOT solve or bypass a CAPTCHA that is already shown —
if a site still challenges a warmed, authenticated session, that workflow simply
isn't a good fit for unattended replay.
"""

# Launch switches that either remove an automation tell or steady the
# fingerprint. `--disable-blink-features=AutomationControlled` is the important
# one: it stops Chromium from setting `navigator.webdriver`, which is the single
# loudest "I'm a bot" signal. The `--enable-automation` disable removes the
# "Chrome is being controlled by automated test software" infobar/flag.
#
# Deliberately NOT included: `--disable-features=IsolateOrigins,site-per-process`.
# It's common in stealth recipes (it eases cross-origin iframe access) but does
# nothing for the automation tells, and it weakens site isolation in a browser
# the user drives manually while signed into their real accounts (the recorder
# profile holds saved logins). Bad trade for zero CAPTCHA benefit.
_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--no-first-run",
]

# Init script injected into every page BEFORE any site JS runs. Patches the
# handful of properties detectors probe most. Kept defensive (try/catch,
# defineProperty guards) so a property that's already non-configurable in a
# given Chromium build can't throw and abort the page's own scripts.
STEALTH_INIT_JS = r"""
(() => {
  const def = (obj, prop, get) => {
    try { Object.defineProperty(obj, prop, { get, configurable: true }); } catch (e) {}
  };

  // 1. navigator.webdriver — the loudest tell. Report undefined like real Chrome.
  def(navigator, 'webdriver', () => undefined);

  // 2. Plugins & mimeTypes — headless reports an empty list; humans don't.
  def(navigator, 'plugins', () => [1, 2, 3, 4, 5]);
  def(navigator, 'mimeTypes', () => [1, 2]);

  // 3. Languages — headless can report []; mirror a normal en-US Chrome.
  def(navigator, 'languages', () => ['en-US', 'en']);

  // 4. window.chrome — present in real Chrome, absent under automation.
  try {
    if (!window.chrome) { window.chrome = {}; }
    if (!window.chrome.runtime) { window.chrome.runtime = {}; }
  } catch (e) {}

  // 5. Permissions.query — headless returns 'denied' for notifications where a
  //    real browser returns 'default'; detectors compare the two.
  try {
    const orig = window.navigator.permissions && window.navigator.permissions.query;
    if (orig) {
      window.navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : orig(params);
    }
  } catch (e) {}

  // 6. WebGL vendor/renderer — headless/SwiftShader leaks "Google SwiftShader".
  //    Report a plausible desktop GPU string instead.
  try {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
      if (p === 37445) return 'Intel Inc.';               // UNMASKED_VENDOR_WEBGL
      if (p === 37446) return 'Intel Iris OpenGL Engine';  // UNMASKED_RENDERER_WEBGL
      return getParam.call(this, p);
    };
  } catch (e) {}
})();
"""


def launch_args(headless: bool) -> list[str]:
    """Chromium launch args for replay: stealth switches, plus --disable-gpu
    when headless (project hard rule — keeps headless replay off the GPU)."""
    args = list(_STEALTH_ARGS)
    if headless:
        args.append("--disable-gpu")
    return args
