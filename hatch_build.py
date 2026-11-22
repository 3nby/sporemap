"""Hatch build hook: builds web/ and puts it in the wheel at sporemap/ui.

src/sporemap/ui is gitignored (vite writes it), so hatch would leave it out without this.

- no npm on the box (like the lxc install): the wheel is built without the UI and
  `sporemap serve` shows its "UI hasn't been built" page. set SPOREMAP_REQUIRE_UI=1
  to make that an error instead, the release workflow does
- SPOREMAP_SKIP_UI=1 skips npm and just packs whatever is already in src/sporemap/ui
- editable installs (pip install -e) never run npm, use `npm run build` in web/ for that
"""
import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class UiHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return  # the sdist carries web/ itself, editable uses the src tree
        root = Path(self.root)
        web = root / "web"
        ui = root / "src" / "sporemap" / "ui"
        need = os.environ.get("SPOREMAP_REQUIRE_UI") == "1"

        if os.environ.get("SPOREMAP_SKIP_UI") != "1" and (web / "package.json").exists():
            npm = shutil.which("npm")
            if npm:
                if not (web / "node_modules").exists():
                    self._run([npm, "ci", "--no-audit", "--no-fund"], web)
                self._run([npm, "run", "build"], web)
            elif need:
                raise RuntimeError("SPOREMAP_REQUIRE_UI=1 but npm isnt installed")
            else:
                self.app.display_warning("npm not found, building sporemap without the web UI")

        if (ui / "index.html").exists():
            build_data["force_include"][str(ui)] = "sporemap/ui"
        elif need:
            raise RuntimeError("SPOREMAP_REQUIRE_UI=1 but " + str(ui) + " has no index.html after the build")
        # print(build_data["force_include"])

    def _run(self, cmd, cwd):
        self.app.display_info("running npm " + " ".join(cmd[1:]) + " in " + str(cwd))
        subprocess.run(cmd, cwd=cwd, check=True)
