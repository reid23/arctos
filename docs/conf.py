import sys
import os

sys.path.insert(0, os.path.abspath(".."))

project = "Arctos"
author = "Arctos Contributors"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

html_theme = "furo"

myst_enable_extensions = [
    "deflist",
    "colon_fence",
]

# Auto-generate heading anchors so that same-page fragment links resolve correctly.
myst_heading_anchors = 4

exclude_patterns = ["_build", "README.md"]

# Make the project's static assets available so that image references in the
# Markdown docs (which are written for the Flask app's root-relative paths) resolve.
html_extra_path = ["../static"]

# Suppress warnings that originate from pre-existing content written for the
# Flask/python-markdown renderer rather than Sphinx/MyST.
suppress_warnings = [
    "myst.xref_missing",  # {#anchor} TOC links in docs.md use python-markdown attr_list syntax
    "myst.header",        # H2→H4 level jumps in docs.md
    "image.not_readable", # covered by html_extra_path above; residual during read phase
]

autodoc_typehints = "description"
autodoc_member_order = "bysource"
