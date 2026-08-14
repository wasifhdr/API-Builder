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

  // 6. WebGL vendor/renderer — with --disable-gpu the headless renderer falls
  //    back to SwiftShader ("ANGLE (Google, Vulkan ... SwiftShader driver)"),
  //    which no desktop Chrome reports. Substitute what THIS machine reports
  //    when driven headful; single-machine deployment, so that is simply the
  //    truth about the browser a human would drive here.
  //
  //    Substituted ONLY when the software renderer is what leaked — headful
  //    recording reports its real GPU untouched. Reporting a value that
  //    contradicts the rest of the fingerprint is worse than reporting none:
  //    these two strings previously named a macOS renderer ("Intel Iris
  //    OpenGL Engine") while the UA said Windows, an impossible pairing that
  //    is more detectable than the SwiftShader string it was hiding.
  //
  //    Update both strings if the deployment moves to another machine — read
  //    them off a headful Chrome there (WEBGL_debug_renderer_info).
  try {
    const VENDOR = 'Google Inc. (AMD)';
    const RENDERER =
      'ANGLE (AMD, AMD Radeon 760M Graphics (0x00001900) Direct3D11 vs_5_0 ps_5_0, D3D11)';
    // WebGL2 has its own prototype; patching only WebGL1 leaves the two
    // disagreeing on the same machine, which is itself a tell.
    for (const ctor of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
      if (!ctor || !ctor.prototype) continue;
      const getParam = ctor.prototype.getParameter;
      ctor.prototype.getParameter = function (p) {
        // 37445 = UNMASKED_VENDOR_WEBGL, 37446 = UNMASKED_RENDERER_WEBGL
        if (p === 37445 || p === 37446) {
          let real = '';
          try { real = String(getParam.call(this, 37446)); } catch (e) {}
          if (real.indexOf('SwiftShader') !== -1) {
            return p === 37445 ? VENDOR : RENDERER;
          }
        }
        return getParam.call(this, p);
      };
    }
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
