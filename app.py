"""Source-mode launcher kept for `python app.py`; the server lives in the riviu package.

Same as `python -m riviu`. User data still resolves to this folder when
RIVIU_DATA_DIR is unset (see riviu/paths.py).
"""

from riviu.__main__ import main

if __name__ == "__main__":
    main()
