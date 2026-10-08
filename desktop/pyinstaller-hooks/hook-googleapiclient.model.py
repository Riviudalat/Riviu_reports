"""Replace the contrib hook, which bundles every static discovery document.

googleapiclient.model reads its own version from the package metadata, and
discovery.build() loads documents/<api>.<version>.json. Ship only the APIs the
app builds (bundle_contents.GOOGLE_DISCOVERY_DOCS).
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bundle_contents import GOOGLE_DISCOVERY_DOCS  # noqa: E402


_documents = collect_data_files(
    "googleapiclient.discovery_cache",
    includes=[f"documents/{name}.json" for name in GOOGLE_DISCOVERY_DOCS],
)
if len(_documents) != len(GOOGLE_DISCOVERY_DOCS):
    raise RuntimeError(f"Missing googleapiclient discovery documents: {GOOGLE_DISCOVERY_DOCS}")
datas = copy_metadata("google_api_python_client") + _documents
