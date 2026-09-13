"""Outillage commun des tests navigateur de la web app PrintPro."""
import time
from types import SimpleNamespace
from pathlib import Path
from playwright.sync_api import sync_playwright

SP = Path("/tmp/printpro-webtests")
FX = SP / "fx"
PAGE = "file:///home/user/printpro/web/index.html"

FAKE_CLAUDE = """
window.__saves = [];
window.__declineNext = false;
window.claude = { use: async (name) => name === "downloads" ? {
  save: async (request) => {
    if (window.__declineNext) { window.__declineNext = false; throw { code: "declined", message: "non" }; }
    const size = request.data instanceof Blob ? request.data.size
      : (request.data.byteLength || String(request.data).length);
    window.__saves.push({ filename: request.filename, size, kind: request.data && request.data.constructor.name });
    return { status: "saved" };
  }
} : null };
"""

IDLE = "!document.getElementById('veil').classList.contains('on')"


class Report:
    def __init__(self):
        self.rows = []
        self.failures = 0

    def check(self, label, ok, detail=""):
        self.rows.append((label, bool(ok), detail))
        if not ok:
            self.failures += 1
        print(("  OK  " if ok else "  FAIL") + f" {label}" + (f" — {detail}" if detail else ""), flush=True)

    def summary(self):
        total = len(self.rows)
        print(f"\n=== {total - self.failures}/{total} contrôles réussis, {self.failures} échec(s) ===")


class App:
    def __init__(self, width=1480, height=1000, fake_claude=False, dark=False, scale=1.0):
        self.play = sync_playwright().start()
        self.browser = self.play.chromium.launch(executable_path="/opt/pw-browsers/chromium",
                                                 args=["--no-sandbox"])
        context = self.browser.new_context(viewport={"width": width, "height": height},
                                           device_scale_factor=scale,
                                           color_scheme="dark" if dark else "light")
        if fake_claude:
            context.add_init_script(FAKE_CLAUDE)
        self.page = context.new_page()
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.goto(PAGE)
        self.page.wait_for_timeout(900)

    def close(self):
        self.browser.close()
        self.play.stop()

    def real_errors(self):
        return [e for e in self.errors if "ERR_CONNECTION" not in e and "fonts.g" not in e]

    # ---- actions
    def wait_idle(self, timeout=240000):
        self.page.wait_for_function(IDLE, timeout=timeout)
        self.page.wait_for_timeout(150)

    def run(self, button_id, timeout=240000):
        started = time.perf_counter()
        self.page.click(f"#{button_id}")
        self.page.wait_for_timeout(120)
        self.wait_idle(timeout)
        return time.perf_counter() - started

    def import_files(self, *paths):
        self.page.set_input_files("#file-input", [str(p) for p in paths])
        self.page.wait_for_timeout(250)
        self.wait_idle()
        self.page.wait_for_timeout(250)

    def tab(self, name):
        self.page.click(f"#tab-{name}")

    def set_range(self, id_, value):
        self.page.fill(f"#{id_}", str(value))
        self.page.dispatch_event(f"#{id_}", "input")

    def select_asset(self, index):
        self.page.locator(".tile .art").nth(index).click()
        self.page.wait_for_timeout(200)

    # ---- lectures
    def text(self, id_):
        return self.page.inner_text(f"#{id_}")

    def flash(self):
        return self.page.inner_text("#flash")

    def current(self):
        data = self.page.evaluate("""() => {
          const a = state.assets.find(x => x.id === state.currentId);
          if (!a) return null;
          const img = a.versions[a.versions.length - 1];
          let opaque = 0, transparent = false;
          for (let i = 3; i < img.data.length; i += 4) { if (img.data[i] > 8) opaque++; if (img.data[i] < 250) transparent = true; }
          return { name: a.name, width: img.width, height: img.height, versions: a.versions.length,
                   history: a.history, hasSvg: !!a.svg, opaque, transparent,
                   svgHead: a.svg ? a.svg.slice(0, 140) : null, count: state.assets.length };
        }""")
        if data is None:
            return SimpleNamespace(count=self.page.evaluate("state.assets.length"), name=None)
        return SimpleNamespace(**data)

    def pixel(self, x, y):
        return self.page.evaluate(f"""() => {{
          const a = state.assets.find(x => x.id === state.currentId);
          const img = a.versions[a.versions.length - 1];
          const i = ({y} * img.width + {x}) * 4;
          return [img.data[i], img.data[i+1], img.data[i+2], img.data[i+3]];
        }}""")
