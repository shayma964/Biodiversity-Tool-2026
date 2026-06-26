# -*- coding: utf-8 -*-
"""
Created on Tue Jun  2 13:37:15 2026

@author: Gebruiker
"""

# -*- coding: utf-8 -*-
"""
Optimized build script for Biodiversity Calculator
Handles all geospatial dependencies correctly
"""
import os
import sys
from pathlib import Path


# === PROJ/GDAL PATH CONFIGURATION ===
def configure_proj_paths():
    """Set PROJ_LIB and PATH for both frozen and dev environments"""
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        proj_lib_path = Path(base_path) / "lib" / "share" / "proj"
        os.environ["PROJ_LIB"] = str(proj_lib_path)
        os.environ["PATH"] = str(Path(base_path) / "lib") + os.pathsep + os.environ["PATH"]
    else:
        env_path = Path(sys.executable).parent
        os.environ["PROJ_LIB"] = str(env_path / "Library" / "share" / "proj")
        os.environ["PATH"] = str(env_path / "Library" / "bin") + os.pathsep + os.environ["PATH"]
def configure_mpl_paths():
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
        mpl_data = base / "lib" / "matplotlib" / "mpl-data"
        if mpl_data.exists():
            os.environ["MATPLOTLIBDATA"] = str(mpl_data)
        cache_dir = base / "mpl_cache"
        cache_dir.mkdir(exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(cache_dir)
    else:
        # Dev environment — point to conda's matplotlib data
        import matplotlib
        os.environ["MATPLOTLIBDATA"] = matplotlib.get_data_path()

configure_proj_paths()
configure_mpl_paths()
# === IMPORTS (after path config) ===
import certifi
import geopandas
import fiona
import pandas
import shapely
import numpy
import matplotlib
import folium
import branca
import jinja2
import markupsafe
from cx_Freeze import setup, Executable
import pyproj

# === PATH SETUP ===
env_root = Path(sys.executable).parent
env_bin = env_root / "Library" / "bin"
geopandas_data = Path(geopandas.__file__).parent / "datasets"
proj_data = env_root / "Library" / "share" / "proj"
fiona_libs = Path(fiona.__file__).parent / ".libs"
script_dir = Path(__file__).parent

# === INCLUDE FILES ===
include_files = []

# ── A) Application data files (CSVs, manuals, logos) ──────────────────────
data_dirs = [
    ("data", "data"),
    ("manuals", "manuals"),
    ("logos", "logos"),
]

for src_dir, dst_dir in data_dirs:
    src_path = script_dir / src_dir
    if src_path.exists():
        for item in src_path.iterdir():
            if item.is_file():
                include_files.append((str(item), f"{dst_dir}/{item.name}"))
                print(f"Added data file: {item.name}")

# ── B) PROJ data ───────────────────────────────────────────────────────────
# ── B) PROJ data ───────────────────────────────────────────────────────────
proj_added = False


pyproj_data = Path(pyproj.__file__).parent / "proj_dir" / "share" / "proj"
if pyproj_data.exists():
    include_files.append((str(pyproj_data), "lib/share/proj"))
    print(f"Added pyproj internal PROJ data from: {pyproj_data}")
    proj_added = True

# Then try conda's PROJ data — but only if pyproj's wasn't found
# (avoids duplicate destination path "lib/share/proj")
if not proj_added:
    if proj_data.exists():
        proj_files = list(proj_data.iterdir())
        include_files.extend(
            (str(f), f"lib/share/proj/{f.name}")
            for f in proj_files if f.is_file()
        )
        print(f"Added {len(proj_files)} PROJ data files from conda PROJ")
        proj_added = True
    else:
        print("WARNING: PROJ data directory not found — CRS operations may fail in .exe")

if not proj_added:
    print("WARNING: No PROJ data found from either pyproj or conda — CRS will fail")

# ── C) GDAL/PROJ DLLs ─────────────────────────────────────────────────────
if env_bin.exists():
    required_dlls = {
        'gdal', 'proj', 'geos', 'sqlite3', 'spatialindex',
        'freexl', 'hdf5', 'netcdf', 'curl', 'openssl',
        'iconv', 'zlib', 'libpng', 'libtiff', 'libjpeg'
    }
    dlls_added = 0
    for dll in env_bin.glob("*.dll"):
        dll_lower = dll.name.lower()
        if any(name in dll_lower for name in required_dlls):
            include_files.append((str(dll), f"lib/{dll.name}"))
            dlls_added += 1
    print(f"Added {dlls_added} GDAL/PROJ DLLs")
    spatialindex_found = [d for d in env_bin.glob("*.dll") if "spatialindex" in d.name.lower()]
    if not spatialindex_found:
        print("WARNING: libspatialindex DLL not found — rtree/geopandas overlay may crash")
else:
    print("WARNING: env_bin not found — geospatial DLLs may be missing")

# ── D) Fiona .libs ─────────────────────────────────────────────────────────
if fiona_libs.exists():
    fiona_lib_files = list(fiona_libs.iterdir())
    include_files.extend(
        (str(f), f"lib/fiona_libs/{f.name}")
        for f in fiona_lib_files
    )
    print(f"Added {len(fiona_lib_files)} Fiona .libs files")

# ── E) GeoPandas datasets ──────────────────────────────────────────────────
if geopandas_data.exists():
    include_files.append((str(geopandas_data), "geopandas/datasets"))
    print("Added GeoPandas datasets")

# ── F) SSL certificates ────────────────────────────────────────────────────
include_files.append((certifi.where(), "lib/certifi/cacert.pem"))
print("Added SSL certificates")

# ── G) Windows system DLLs ────────────────────────────────────────────────
windows_dlls = [
    r"C:\Windows\System32\api-ms-win-core-path-l1-1-0.dll",
]
for dll in windows_dlls:
    if os.path.exists(dll):
        include_files.append((dll, f"lib/{os.path.basename(dll)}"))
        print(f"Added Windows DLL: {os.path.basename(dll)}")

# ── H) Python runtime DLLs ────────────────────────────────────────────────
python_dlls = [
    "python3.dll",
    f"python{sys.version_info.major}{sys.version_info.minor}.dll",
    "vcruntime140.dll",
    "msvcp140.dll",
    "vcruntime140_1.dll",
]
for dll_name in python_dlls:
    dll_path = env_root / dll_name
    if dll_path.exists():
        include_files.append((str(dll_path), f"lib/{dll_name}"))
        print(f"Added Python DLL: {dll_name}")

# ── I) Tkinter DLLs (for NavigationToolbar2Tk) ────────────────────────────
tk_dll_names = [
    "tcl86t.dll", "tk86t.dll",   # Python 3.8–3.11 typical names
    "tcl87t.dll", "tk87t.dll",   # Python 3.12+
    "tcl86.dll",  "tk86.dll",    # alternate naming
]
for dll_name in tk_dll_names:
    for search_dir in [env_root / "Library" / "bin", env_root / "DLLs"]:
        dll_path = search_dir / dll_name
        if dll_path.exists():
            include_files.append((str(dll_path), f"lib/{dll_name}"))
            print(f"Added Tkinter DLL: {dll_name}")
            break

# ── J) Matplotlib data (fonts, style sheets, color maps) ──────────────────
mpl_data = Path(matplotlib.get_data_path())
if mpl_data.exists():
    include_files.append((str(mpl_data), "lib/matplotlib/mpl-data"))
    print(f"Added matplotlib mpl-data from: {mpl_data}")
else:
    print("WARNING: matplotlib mpl-data not found — fonts and styles may be missing")

# ── K) Folium templates ────────────────────────────────────────────────────
folium_root = Path(folium.__file__).parent
for folder in ["templates"]:
    p = folium_root / folder
    if p.exists():
        include_files.append((str(p), f"folium/{folder}"))
        print(f"Added folium/{folder}")
    else:
        print(f"WARNING: folium/{folder} not found")

# ── L) Branca templates and static files (folium HTML engine) ─────────────
branca_root = Path(branca.__file__).parent
for folder in ["templates", "static"]:
    p = branca_root / folder
    if p.exists():
        include_files.append((str(p), f"branca/{folder}"))
        print(f"Added branca/{folder}")
    else:
        print(f"WARNING: branca/{folder} not found")

# ── M) Jinja2 package (template engine — includes all .py and filters) ─────
jinja2_root = Path(jinja2.__file__).parent
if jinja2_root.exists():
    include_files.append((str(jinja2_root), "jinja2"))
    print(f"Added jinja2 package from: {jinja2_root}")
else:
    print("WARNING: jinja2 root not found")

# ── N) Markupsafe (C extension used by jinja2, often missed) ──────────────
markupsafe_root = Path(markupsafe.__file__).parent
if markupsafe_root.exists():
    include_files.append((str(markupsafe_root), "markupsafe"))
    print(f"Added markupsafe from: {markupsafe_root}")
else:
    print("WARNING: markupsafe not found — jinja2/folium may crash")

# === BUILD OPTIONS ===
build_options = {
    "packages": [
        # Core Python
        "os", "sys", "pathlib", "csv", "time",
        "webbrowser", "subprocess", "platform", "shutil", "tempfile",
        "queue", "threading", "collections", "datetime",
        "json", "re", "math", "copy", "warnings", "pydoc",

        # GUI and imaging
        "tkinter", "PIL", "PIL._tkinter_finder",

        # Data processing
        "pandas", "numpy",

        # Geospatial
        "geopandas", "shapely", "fiona", "pyproj", "rtree",
        "shapely.geometry", "shapely.validation",

        # DXF handling
        "ezdxf",
        "ezdxf.layouts",
        "ezdxf.entities",
        # Matplotlib — all backends needed
        "matplotlib",
        "matplotlib.pyplot",
        "matplotlib.patches",
        "matplotlib.colors",
        "matplotlib.backends",
        "matplotlib.backends.backend_agg",
        "matplotlib.backends.backend_tkagg",
        

        # Folium and its dependencies
        "folium",
        "folium.plugins",
        "branca",
        "branca.element",
        "jinja2",
        "jinja2.ext",
        "markupsafe",

        # Network / SSL
        "urllib3", "certifi",
    ],

    "includes": [
        # Tkinter
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",

        # PIL
        "PIL.Image",
        "PIL.ImageTk",
        "PIL._imaging",
        

        # Geopandas / shapely / fiona
        "geopandas.io",
        "shapely.geos",
        "fiona.ogrext",

        # Pandas / numpy internals
        "pandas._libs",
        "numpy.core._multiarray_umath",

        # Matplotlib internals (often missed by cx_Freeze)
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends.backend_agg",
        "matplotlib.figure",
        "matplotlib.axes",
        "matplotlib.colors",
        "matplotlib.patches",

        # Jinja2 / markupsafe internals
        "markupsafe._speedups",
        "jinja2.ext",
        "jinja2.filters",
        "jinja2.tests",
        "jinja2.utils",
    ],

    "include_files": include_files,

    "excludes": [
        # Heavy unused packages
        "PyQt5", "qtpy", "PySide2", "PySide6",
        "IPython", "jupyter", "notebook",
        # Test frameworks
        "test", "tests", "tkinter.test", "unittest",
    ],

    "optimize": 0,        # Use 1 not 2 — level 2 strips docstrings which can
                          # break packages that rely on __doc__ at runtime
    "include_msvcr": True,
    "silent": False,
    "build_exe": "build/biodiversity_calculator",
}

# === ICON ===
icon_paths = [
    script_dir / "logos" / "biodiversity.ico",
    script_dir / "biodiversity.ico",
    script_dir / "icon.ico",
]
icon_file = None
for icon in icon_paths:
    if icon.exists():
        icon_file = str(icon)
        print(f"Using icon: {icon_file}")
        break
if not icon_file:
    print("Warning: No icon file found — using default")

# === EXECUTABLE ===
base = "gui" if sys.platform == "win32" else None
executables = [
    Executable(
        script="BiodiversityTool_2026JuneVersion.py",
        base=base,
        target_name="BiodiversityCalculator.exe",
        icon=icon_file,
        copyright="Copyright (c) 2026",
        trademarks="Biodiversity Tool",
    )
]

# === SETUP ===
setup(
    name="Biodiversity Calculator",
    version="1.0.0",
    description="Biodiversity Metric Calculator for Habitat Assessment",
    author="Shaymaa Yousef S. Hammash",
    author_email="shaymapal@gmail.com",
    options={"build_exe": build_options},
    executables=executables,
)

# === BUILD SUMMARY ===
print("\n" + "=" * 55)
print("Build configuration complete!")
print("=" * 55)
print(f"Total included files: {len(include_files)}")
print(f"Output directory:     {build_options['build_exe']}")
print(f"Target executable:    BiodiversityCalculator.exe")
print("=" * 55)
print("\nPost-build reminder:")
print("  Run the .exe from cmd.exe on first test so errors")
print("  print to console instead of silently disappearing.")
print("=" * 55)