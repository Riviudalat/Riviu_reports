"""PyInstaller entry script for the riviu-server sidecar (see build_sidecar.py).

It sits outside the package so the frozen app imports riviu.desktop_server as
a package module, exactly as source mode does.
"""

from riviu.desktop_server import main

if __name__ == "__main__":
    main()
