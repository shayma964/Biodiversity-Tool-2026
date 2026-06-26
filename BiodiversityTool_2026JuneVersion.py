# -*- coding: utf-8 -*-
"""
Created on Fri May 29 16:12:36 2026
@author: Gebruiker
"""

import os
import sys
os.environ["MPLBACKEND"] = "Agg"  # MUST be before any other import

import shutil
import csv
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import webbrowser
import subprocess
import platform

import pandas as pd
import geopandas as gpd
import numpy as np
import math as _math
import shapely
from shapely.validation import make_valid
from shapely.geometry import Polygon, box
from shapely.geometry import MultiPolygon
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.affinity import translate

import ezdxf
from pyproj import CRS

# -------------------- Configuration & Paths --------------------
def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent

BASE_DIR = get_base_dir()
DATA_DIR = BASE_DIR / "data"
LOGOS_DIR = BASE_DIR / "logos"
MANUAL_DIR = BASE_DIR / "manuals"
HABITATS_CSV = DATA_DIR / "all_habitats.csv"
YEARS_CSV = DATA_DIR / "target_year.csv"
ENG_MANUAL = MANUAL_DIR / "manual_english.pdf"
ND_MANUAL = MANUAL_DIR / "manual_nederlands.pdf"

MAIN_BG = "#f1eef6"

CONDITION_MAPPING   = {"Good": 3.0, "Fairly Good": 2.5, "Moderate": 2.0, "Fairly poor": 1.5, "Poor": 1.0}
DIFFICULTY_MAPPING  = {"Very high": 0.1, "High": 0.33, "Medium": 0.67, "Low": 1.0}
SPATIAL_MAPPING     = {"On-site": 1.0, "Within same city": 0.75, "Somewhere further": 0.5}
STRATEGIC_MAPPING   = {"High": 1.15, "Medium": 1.1, "Low": 1.0}
DISTINCTIVENESS_MAP = {"V.High": 8, "High": 6, "Medium": 4, "Low": 2, "V.Low": 0}

# ── Expected column names — change here if your files use different names ──
COL_BROAD     = "Baseline Broad Habitat Type"
COL_CONDITION = "Baseline Condition"
COL_DISTINCT  = "Baseline Distinctiveness"
COL_STRATEGIC = "Baseline Strategic Significance"
COL_HABITAT   = "Baseline Habitat Type"
COL_PARCEL    = "Parcel Ref"
COL_AREA      = "Area"
REQUIRED_LOSS_COLS  = [COL_BROAD, COL_CONDITION, COL_DISTINCT]
REQUIRED_BASE_COLS  = [COL_PARCEL, COL_BROAD, COL_HABITAT, COL_AREA, COL_CONDITION, COL_STRATEGIC]

# -------------------- Path helpers (call BEFORE matplotlib) --------------------
def configure_proj_paths():
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
        os.environ["PROJ_LIB"] = str(base / "lib" / "share" / "proj")
        os.environ["PATH"]     = str(base / "lib") + os.pathsep + os.environ["PATH"]
    else:
        env_path = Path(sys.executable).parent
        os.environ["PROJ_LIB"] = str(env_path / "Library" / "share" / "proj")
        os.environ["PATH"]     = str(env_path / "Library" / "bin") + os.pathsep + os.environ["PATH"]

def configure_mpl_paths():
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
        mpl_data = base / "lib" / "matplotlib" / "mpl-data"
        if mpl_data.exists():
            os.environ["MATPLOTLIBDATA"] = str(mpl_data)
        cache_dir = base / "mpl_cache"
        cache_dir.mkdir(exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(cache_dir)

configure_proj_paths()
configure_mpl_paths()

# -------------------- Matplotlib (after path config) --------------------
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.colors as mcolors

# -------------------- Folium (after path config) --------------------
import folium
import tempfile

# -------------------------------------------------------------------------
class AutocompleteCombobox(ttk.Combobox):
    def set_completion_list(self, completion_list):
        self._completion_list = sorted(completion_list, key=str.lower)
        self["values"] = self._completion_list
        self.bind("<KeyRelease>", self._filter_list)

    def _filter_list(self, event):
        typed = self.get().lower()
        if typed == "":
            self["values"] = self._completion_list
            return
        self["values"] = [i for i in self._completion_list if typed in i.lower()]

# -------------------- Logo Manager --------------------
class LogoManager:
    def __init__(self, logos_dir: Path):
        self.logos_dir = logos_dir
        self.cache = {}

    def load(self, name: str, max_w=160, max_h=80):
        if name in self.cache:
            return self.cache[name]
        for e in [".png", ".jpg", ".jpeg", ".gif", ".ico"]:
            p = self.logos_dir / f"{name}{e}"
            if p.exists():
                try:
                    img = Image.open(p)
                    img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self.cache[name] = photo
                    return photo
                except Exception as exc:
                    print(f"Error loading logo {p}: {exc}")
        self.cache[name] = None
        return None

# -------------------- Geospatial helpers --------------------
def force_polygon(geom):
    if geom is None or geom.is_empty:
        return None
    try:
        if geom.type in ["Polygon", "MultiPolygon"]:
            return geom
        if geom.type == "LineString":
            coords = list(geom.coords)
            if len(coords) >= 3:
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                if len(coords) >= 4:
                    poly = Polygon(coords)
                    if poly.is_valid:
                        return poly
        if geom.type == "MultiLineString":
            polys = []
            for line in geom.geoms:
                coords = list(line.coords)
                if len(coords) >= 3:
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    if len(coords) >= 4:
                        poly = Polygon(coords)
                        if poly.is_valid:
                            polys.append(poly)
            if polys:
                mp = MultiPolygon(polys)
                if mp.is_valid:
                    return mp
    except Exception as e:
        print(f"Warning force_polygon: {e}")
    return geom

def load_and_fix(path):
    gdf = gpd.read_file(path)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise RuntimeError("No valid geometries found")
    try:
        gdf["geometry"] = gdf.geometry.buffer(0)
    except Exception:
        pass
    try:
        gdf["geometry"] = gdf.geometry.apply(make_valid)
    except Exception:
        pass
    try:
        gdf["geometry"] = gdf.geometry.apply(force_polygon)
    except Exception:
        pass
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise RuntimeError("All geometries invalid after cleaning")
    return gdf

def convert_dxf_layers(input_path, output_shp, target_crs=None):
    """Convert DXF to Shapefile. Handles LWPOLYLINE, POLYLINE, HATCH, CIRCLE."""
   
    try:
        doc = ezdxf.readfile(input_path)
        msp = doc.modelspace()
        dxf_native_epsg = None
        geo_data = msp.get_geodata()
        if geo_data:
            try:
                _, dxf_native_epsg = geo_data.get_crs_transformation()
                print(f"🌲 Detected georeferenced DXF CRS: EPSG:{dxf_native_epsg}")
            except Exception:
                pass
        all_geometries = []
        for entity in msp:
            if not hasattr(entity, "dxftype"):
                continue
            dtype = entity.dxftype()

            # Polylines (most common)
            if dtype in ["LWPOLYLINE", "POLYLINE"]:
                points = []
                if dtype == "LWPOLYLINE":
                    pts = list(entity.get_points())
                    points = [(p[0], p[1]) for p in pts]
                else:
                    for v in entity.vertices:
                        points.append((v.dxf.location.x, v.dxf.location.y))
                if len(points) >= 3:
                    if points[0] != points[-1]:
                        points.append(points[0])
                    poly = Polygon(points)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if poly.is_valid and not poly.is_empty:
                        all_geometries.append(poly)

            # Hatch (filled areas — common for planned footprints)
            elif dtype == "HATCH":
                try:
                    for path in entity.paths:
                        pts = []
                        if hasattr(path, "vertices"):
                            pts = [(v[0], v[1]) for v in path.vertices]
                        elif hasattr(path, "edges"):
                            for edge in path.edges:
                                if hasattr(edge, "start"):
                                    pts.append((edge.start[0], edge.start[1]))
                        if len(pts) >= 3:
                            if pts[0] != pts[-1]:
                                pts.append(pts[0])
                            poly = Polygon(pts)
                            if not poly.is_valid:
                                poly = poly.buffer(0)
                            if poly.is_valid and not poly.is_empty:
                                all_geometries.append(poly)
                except Exception:
                    pass

            # Circle
            elif dtype == "CIRCLE":
                try:
                    cx = entity.dxf.center.x
                    cy = entity.dxf.center.y
                    r  = entity.dxf.radius
                    pts = [(cx + r * _math.cos(2*_math.pi*i/64),
                            cy + r * _math.sin(2*_math.pi*i/64)) for i in range(64)]
                    poly = Polygon(pts)
                    if poly.is_valid:
                        all_geometries.append(poly)
                except Exception:
                    pass

        if not all_geometries:
            raise RuntimeError(
                "No valid geometries found in DXF.\n\n"
                "Supported entity types: LWPOLYLINE, POLYLINE, HATCH, CIRCLE.\n"
                "Make sure your CAD file uses one of these for the development footprint.")

        # Normalize orientation — DXF polylines are often clockwise which causes
        # shapely to treat the exterior as interior, flipping the intersection.
        # orient() forces counter-clockwise exterior ring (shapely convention).
        
        normalized = []
        for geom in all_geometries:
            try:
                # buffer(0) fixes self-intersections, orient fixes winding order
                geom = geom.buffer(0)
                
                # shapely.geometry.polygon.orient forces CCW exterior
                if hasattr(shapely.geometry.polygon, "orient"):
                    geom = shapely.geometry.polygon.orient(geom, sign=1.0)
                normalized.append(geom)
            except Exception:
                normalized.append(geom)

        # Dissolve all geometries into one footprint to avoid duplicate intersections
        if len(normalized) > 1:
            merged = unary_union(normalized)
            if merged.is_valid:
                normalized = [merged]

        gdf = gpd.GeoDataFrame(geometry=normalized)
        if dxf_native_epsg:
            # 1. First assign the native CRS found inside the CAD drawing metadata
            gdf = gdf.set_crs(epsg=dxf_native_epsg)
            
            # 2. Safely reproject the dataset to match your baseline target map projection
            if target_crs and (gdf.crs != target_crs):
                print(f"🔄 Automatically reprojecting DXF from EPSG:{dxf_native_epsg} to match baseline.")
                gdf = gdf.to_crs(target_crs)
        else:
            # Fallback label assignment if the drawing lacks georeferencing tags
            if target_crs:
                gdf = gdf.set_crs(target_crs)
        
        gdf.to_file(output_shp)
        return output_shp
    except Exception as e:
        raise RuntimeError(f"DXF conversion failed: {e}")

def convert_if_needed(input_path, is_baseline=False, target_crs=None):
    ext = os.path.splitext(input_path)[1].lower()
    if is_baseline:
        if ext in (".shp", ".gpkg"):
            return input_path
        raise RuntimeError("Baseline must be .shp or .gpkg")
    else:
        if ext == ".dxf":
            out = os.path.splitext(input_path)[0] + "_conv.shp"
            return convert_dxf_layers(input_path, out, target_crs=target_crs)
        if ext == ".shp":
            return input_path
        raise RuntimeError("Planned development must be .shp or .dxf")
# After loading gdf1 and gdf2, add this:

def validate_dxf_position(
    baseline_gdf,
    planned_gdf,
    source_file=None,
    
):
    """
    Validate that a DXF-derived planned development layer is
    spatially aligned with the baseline layer.

    Parameters
    ----------
    baseline_gdf : GeoDataFrame
    planned_gdf : GeoDataFrame
    source_file : str | None
        Original input file path.
    warn_distance : float
        Distance threshold (m) above which a warning is shown.

    Returns
    -------
    bool
        True if alignment appears valid.
        False if a potential positioning issue was detected.
    """
    warn_distance = max(
    baseline_gdf.total_bounds[2] - baseline_gdf.total_bounds[0],
    baseline_gdf.total_bounds[3] - baseline_gdf.total_bounds[1],
    )
    if baseline_gdf.empty or planned_gdf.empty:
        return False

    base_union = unary_union(baseline_gdf.geometry)
    plan_union = unary_union(planned_gdf.geometry)

    distance = base_union.distance(plan_union)

    # perfectly fine
    if distance <= warn_distance:
        return True

    base_bbox = box(*baseline_gdf.total_bounds)
    plan_bbox = box(*planned_gdf.total_bounds)

    bbox_intersects = base_bbox.intersects(plan_bbox)

    base_centroid = base_union.centroid
    plan_centroid = plan_union.centroid

    dx = plan_centroid.x - base_centroid.x
    dy = plan_centroid.y - base_centroid.y

    msg = (
        "The planned development does not overlap the baseline.\n\n"
        f"Distance between datasets: {distance:.2f} m\n\n"
        f"Centroid offset:\n"
        f"ΔX = {dx:.2f} m\n"
        f"ΔY = {dy:.2f} m\n\n"
        f"Bounding boxes overlap: {'Yes' if bbox_intersects else 'No'}\n\n"
        "Possible causes:\n"
        "• Local CAD coordinate system\n"
        "• Missing georeferencing in DXF\n"
        "• Block reference (INSERT) transformations\n"
        "• Incorrect CRS assignment\n\n"
        "Please verify the DXF position in QGIS or AutoCAD."
    )

    print("\n===== DXF POSITION VALIDATION =====")
    print(f"Distance: {distance:.2f} m")
    print(f"Centroid ΔX: {dx:.2f}")
    print(f"Centroid ΔY: {dy:.2f}")
    print(f"BBOX overlap: {bbox_intersects}")

    messagebox.showwarning(
        "Possible DXF Positioning Issue",
        msg
    )

    return False


def handle_planned_development(planned_path, baseline_gdf):
    """Handle planned development (SHP or DXF) - load, reproject, and fix alignment if needed."""
    
    is_dxf = planned_path.lower().endswith(".dxf")
    
    # Load file
    if is_dxf:
        # CHANGED: No user prompt needed. Pass baseline_gdf.crs directly.
        # The converter handles extraction and reprojection automatically.
        shp_path = planned_path.replace('.dxf', '_conv.shp')
        
        # CHANGED: Parameter renamed from crs_epsg to target_crs to match updated helper
        convert_dxf_layers(planned_path, shp_path, target_crs=baseline_gdf.crs)
        planned_gdf = load_and_fix(shp_path)
    else:
        planned_gdf = load_and_fix(planned_path)
    
    # Reproject Shapefiles if their native CRS doesn't match baseline
    # (DXF is already handled during conversion, but this acts as a safe catch-all)
    if (
    planned_gdf.crs is not None
    and baseline_gdf.crs is not None
    and not CRS(planned_gdf.crs).equals(CRS(baseline_gdf.crs))
    ):
       planned_gdf = planned_gdf.to_crs(baseline_gdf.crs)
    
    # Fix alignment for DXF only (will safely exit if already georeferenced)
    if is_dxf:
        valid = validate_dxf_position(
        baseline_gdf=baseline_gdf,
        planned_gdf=planned_gdf,
        source_file=planned_path
    )

        if not valid:
          raise RuntimeError(
            "DXF appears misaligned with baseline. "
            "Please verify in QGIS or AutoCAD."
        )
    return planned_gdf
def _safe_union(gdf):
    try:
        return gdf.geometry.union_all()
    except AttributeError:
        return gdf.geometry.unary_union
# -------------------- App Class --------------------
class BiodiversityApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Biodiversity Tool")
        self.root.geometry("1200x800")
        self.root.configure(bg=MAIN_BG)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".",             background=MAIN_BG, foreground="#016c59", font=("Segoe UI", 10))
        style.configure("TFrame",        background=MAIN_BG)
        style.configure("TLabelframe",   background=MAIN_BG)
        style.configure("TLabelframe.Label", background=MAIN_BG)
        style.configure("TLabel",        background=MAIN_BG, foreground="#016c59")
        style.configure("TButton",       background="#1c9099", foreground="#f1eef6", padding=6)
        style.map("TButton",             background=[("active", "#016c59")])
        style.configure("TCombobox",     fieldbackground=MAIN_BG, background=MAIN_BG, foreground="#016c59")

        ico_try = LOGOS_DIR / "biodiversity.ico"
        try:
            if ico_try.exists():
                self.root.iconbitmap(str(ico_try))
            else:
                alt = BASE_DIR / "biodiversity.ico"
                if alt.exists():
                    self.root.iconbitmap(str(alt))
        except Exception:
            pass

        self.logo_manager = LogoManager(LOGOS_DIR)
        self.habitats_df  = self._load_habitats()
        self.years_df     = self._load_years()

        self.saved_rows              = []
        self.gain_items              = []
        self.gain_total              = 0.0
        self.baseline_df             = None
        self.current_parcel_data     = None
        self.mapped_specific_habitat = None
        self.mapped_strategic        = None
        self.gain_tree               = None
        self.gain_total_label        = None

        self._map_gdf1         = None
        self._map_gdf2         = None
        self._map_intersection = None

        self._build_ui()

    # -------------------- Column & Habitat mapping dialogs --------------------
    def _show_column_mapping_dialog(self, available_cols, required_cols):
        """Modal dialog: let user map their column names to the expected ones."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Map Column Names")
        dialog.geometry("560x380")
        dialog.resizable(False, False)
        dialog.grab_set()

        ttk.Label(dialog,
            text="Some expected columns were not found in your file.\n"
                 "Please map your columns to the required ones (or leave as '-- skip --'):",
            font=("Segoe UI", 10), justify="left").pack(anchor="w", padx=15, pady=(12, 8))

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)

        mapping_vars = {}
        sorted_avail = ["-- skip --"] + sorted(available_cols)
        for i, req_col in enumerate(required_cols):
            ttk.Label(frame, text=f"{req_col}:", width=38,
                      anchor="w").grid(row=i, column=0, sticky="w", padx=8, pady=4)
            var = tk.StringVar(value=req_col if req_col in available_cols else "-- skip --")
            cb  = ttk.Combobox(frame, textvariable=var, values=sorted_avail,
                               state="readonly", width=30)
            cb.grid(row=i, column=1, sticky="w", padx=8, pady=4)
            mapping_vars[req_col] = var

        result = {}

        def on_confirm():
            for req, var in mapping_vars.items():
                val = var.get()
                if val and val != "-- skip --":
                    result[req] = val
            dialog.destroy()

        btn_f = ttk.Frame(dialog)
        btn_f.pack(fill="x", pady=10)
        ttk.Button(btn_f, text="Confirm", command=on_confirm).pack(side="left", padx=15)
        ttk.Button(btn_f, text="Cancel",  command=dialog.destroy).pack(side="left")
        dialog.wait_window()
        return result

    def _show_habitat_mapping_dialog(self, unknown_habitats, known_habitats):
        """Modal dialog: map unrecognised / foreign-language habitat names to known ones."""
        if not unknown_habitats:
            return {}

        dialog = tk.Toplevel(self.root)
        dialog.title("Map Habitat Names")
        dialog.geometry("660x460")
        dialog.grab_set()

        ttk.Label(dialog,
            text="Some habitat names were not recognised in the database.\n"
                 "Map them to known habitats, or leave as '-- skip --' to ignore:",
            font=("Segoe UI", 10), justify="left").pack(anchor="w", padx=15, pady=(12, 8))

        outer  = ttk.Frame(dialog)
        outer.pack(fill="both", expand=True, padx=10)
        canvas = tk.Canvas(outer, highlightthickness=0)
        sb     = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner  = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        mapping_vars = {}
        sorted_known = ["-- skip --"] + sorted(known_habitats)
        for i, uh in enumerate(unknown_habitats):
            ttk.Label(inner, text=uh, width=32, anchor="w",
                      foreground="#c0392b").grid(row=i, column=0, sticky="w", padx=8, pady=3)
            ttk.Label(inner, text="→").grid(row=i, column=1, padx=4)
            var = tk.StringVar(value="-- skip --")
            cb  = ttk.Combobox(inner, textvariable=var, values=sorted_known,
                               state="readonly", width=40)
            cb.grid(row=i, column=2, sticky="w", padx=8, pady=3)
            mapping_vars[uh] = var

        result = {}

        def on_confirm():
            for uh, var in mapping_vars.items():
                val = var.get()
                if val != "-- skip --":
                    result[uh] = val
            dialog.destroy()

        ttk.Button(dialog, text="Confirm", command=on_confirm).pack(pady=8)
        dialog.wait_window()
        return result

   

    # -------------------- Help menu --------------------
    def _open_url(self, url: str):
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Open URL failed", f"Could not open URL:\n{url}\n\n{e}")

    def _open_local_pdf(self, pdf_path: Path):
        if getattr(sys, "frozen", False):
            base_dir      = Path(sys.executable).parent
            corrected_path = base_dir / "manuals" / pdf_path.name
        else:
            corrected_path = pdf_path
        if not corrected_path.exists():
            messagebox.showerror("File not found",
                f"Manual not found:\n{corrected_path}\n\nMake sure it is stored in the /manuals folder.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(corrected_path))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(corrected_path)], check=False)
            else:
                subprocess.run(["xdg-open", str(corrected_path)], check=False)
        except Exception:
            try:
                webbrowser.open(f"file://{corrected_path}")
            except Exception:
                messagebox.showerror("Error", f"Could not open manual:\n{corrected_path}")

    def _show_about_window(self):
        about = tk.Toplevel(self.root)
        about.title("About This Tool")
        about.geometry("620x420")
        about.resizable(False, False)
        text = (
            "Biodiversity Metric Calculator\n"
            "--------------------------------------\n"
            "This tool was developed by:\n"
            "   Shaymaa Yousef S. Hammash\n"
            "   Bio-engineer and Master's Student\n"
            "   Ghent University (2024-2026)\n\n"
            "Contact:\n"
            "   shaymapal@gmail.com\n\n"
            "Sources & License:\n"
            "   Based on the UK Biodiversity Metric by DEFRA.\n"
            "   Open Government Licence (OGL v3.0):\n"
            "   https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/\n\n"
            "Developer notes:\n"
            "   This tool was made to assist campus planners at Ghent University\n"
            "   to evaluate the impact of new construction on biodiversity level\n"
            "   on campus and to assess added biodiversity value of new habitats."
        )
        txt = tk.Text(about, wrap="word", font=("Segoe UI", 10), padx=12, pady=12)
        txt.insert("1.0", text)
        txt.config(state="disabled")
        txt.pack(expand=True, fill="both")
        ttk.Button(about, text="Close", command=about.destroy).pack(pady=10)

    # -------------------- Data loading --------------------
    def _load_habitats(self):
        if HABITATS_CSV.exists():
            try:
                df = pd.read_csv(HABITATS_CSV, dtype=str).fillna("")
                if "Specific Habitat" not in df.columns or "Broad Habitat Type" not in df.columns:
                    raise Exception("missing headers")
                if "Distinctiveness Category" not in df.columns:
                    df["Distinctiveness Category"] = ""
                df["Distinctiveness Score"] = (
                    df["Distinctiveness Category"].map(DISTINCTIVENESS_MAP).fillna(0).astype(float)
                )
                return df
            except Exception as e:
                print("Reading habitats failed:", e)
        sample = pd.DataFrame([
            {"Broad Habitat Type": "Grassland", "Specific Habitat": "Improved grassland",   "Distinctiveness Category": "Medium"},
            {"Broad Habitat Type": "Woodland",  "Specific Habitat": "Broadleaved woodland", "Distinctiveness Category": "High"},
        ])
        sample["Distinctiveness Score"] = sample["Distinctiveness Category"].map(DISTINCTIVENESS_MAP).fillna(0).astype(float)
        return sample

    def _load_years(self):
        if YEARS_CSV.exists():
            try:
                df = pd.read_csv(YEARS_CSV, dtype=str).fillna("")
                df["Multiplier"] = pd.to_numeric(df.get("Multiplier", pd.Series()), errors="coerce").fillna(1.0)
                return df
            except Exception as e:
                print("Reading years failed:", e)
        return pd.DataFrame([{"Years": "5", "Multiplier": 1.05}, {"Years": "10", "Multiplier": 1.0}])

    # -------------------- UI build --------------------
    def _build_ui(self):
        style = ttk.Style()
        style.configure("TNotebook",    background=MAIN_BG)
        style.configure("Card.TFrame",  background=MAIN_BG)

        # ── Menu bar ──────────────────────────────────────────────────
        menubar   = tk.Menu(self.root)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="📘 Manual (English)",
            command=lambda: self._open_local_pdf(ENG_MANUAL))
        help_menu.add_command(label="📙 Handleiding (Nederlands)",
            command=lambda: self._open_local_pdf(ND_MANUAL))
        help_menu.add_separator()
        help_menu.add_command(label="🌐 Biodiversity Metric Reference (UK)",
            command=lambda: webbrowser.open(
                "https://assets.publishing.service.gov.uk/media/689c5ee17b2e384441636196/"
                "The_Statutory_Biodiversity_Metric_-_User_Guide_-_July_2025.pdf"))
        help_menu.add_command(label="📄 Open Government Licence (UK)",
            command=lambda: webbrowser.open(
                "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"))
        help_menu.add_command(label="ℹ️ About This Tool", command=self._show_about_window)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

        # ── Notebook (fills all space) ────────────────────────────────
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(4, 6))

        self.tab_loss     = ttk.Frame(self.notebook)
        self.tab_gain     = ttk.Frame(self.notebook)
        self.tab_baseline = ttk.Frame(self.notebook)
        self.tab_saved    = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_loss,     text="🏞️ Loss Calculator")
        self.notebook.add(self.tab_gain,     text="📈 Gain Calculator")
        self.notebook.add(self.tab_baseline, text="🔄 Baseline Comparison")
        self.notebook.add(self.tab_saved,    text="📋 Saved Results")

        self._build_loss_tab()
        self._build_gain_tab()
        self._build_baseline_tab()
        self._build_saved_tab()

    # -------------------- Loss Tab --------------------
    def _build_loss_tab(self):
        pane = tk.PanedWindow(self.tab_loss, orient="horizontal", bg=MAIN_BG, sashwidth=6)
        pane.pack(fill="both", expand=True, padx=8, pady=8)

        # ── Left panel ───────────────────────────────────────────────
        left = ttk.Frame(pane)
        pane.add(left, minsize=420)

        card = ttk.Frame(left, padding=12)
        card.pack(fill="x", padx=4, pady=4)

        ttk.Label(card, text="File Selection", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))

        baseline_frame = ttk.Frame(card)
        baseline_frame.pack(fill="x", pady=4)
        ttk.Label(baseline_frame, text="Baseline Habitat File:").pack(side="left", padx=(0, 8))
        self.loss_baseline_path = tk.StringVar()
        ttk.Entry(baseline_frame, textvariable=self.loss_baseline_path, width=42).pack(side="left", padx=(0, 6))
        ttk.Button(baseline_frame, text="Browse",
            command=lambda: self._browse_file(self.loss_baseline_path,
                [("Shapefiles", "*.shp"), ("GeoPackage", "*.gpkg")])).pack(side="left")

        planned_frame = ttk.Frame(card)
        planned_frame.pack(fill="x", pady=4)
        ttk.Label(planned_frame, text="Planned Development File:").pack(side="left", padx=(0, 8))
        self.loss_planned_path = tk.StringVar()
        ttk.Entry(planned_frame, textvariable=self.loss_planned_path, width=42).pack(side="left", padx=(0, 6))
        ttk.Button(planned_frame, text="Browse",
            command=lambda: self._browse_file(self.loss_planned_path,
                [("Shapefiles", "*.shp"), ("DXF Files", "*.dxf")])).pack(side="left")

        sig_frame = ttk.Frame(card)
        sig_frame.pack(fill="x", pady=4)
        ttk.Label(sig_frame, text="Strategic Significance:").pack(side="left", padx=(0, 8))
        self.loss_significance = tk.StringVar(value="1.0")
        ttk.Entry(sig_frame, textvariable=self.loss_significance, width=10).pack(side="left", padx=(0, 6))
        ttk.Label(sig_frame, text="(1.0 = Low, 1.15 = High)").pack(side="left")

        btn_row = ttk.Frame(card)
        btn_row.pack(fill="x", pady=8)
        ttk.Button(btn_row, text="Calculate Biodiversity Loss",
            command=self._process_and_export_loss).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="🗺️ Show Interactive Map",
            command=self._show_interactive_map).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="💾 Export Static Map",
            command=self._export_static_map).pack(side="left")

        results_card = ttk.Frame(left, padding=12)
        results_card.pack(fill="both", expand=True, padx=4, pady=4)
        ttk.Label(results_card, text="Results", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 6))
        self.loss_results_text = tk.Text(results_card, height=12, wrap="word")
        self.loss_results_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(results_card, orient="vertical", command=self.loss_results_text.yview)
        sb.pack(side="right", fill="y")
        self.loss_results_text.configure(yscrollcommand=sb.set)

        # ── Right panel (map) — use grid for precise row control ────────
        right = ttk.Frame(pane)
        right.grid_rowconfigure(1, weight=1)    # only canvas row expands
        right.grid_columnconfigure(0, weight=1)
        pane.add(right, minsize=580)

        map_header = ttk.Frame(right)
        map_header.grid(row=0, column=0, sticky="ew", padx=6, pady=(8, 2))
        ttk.Label(map_header, text="Map View", font=("Segoe UI", 12, "bold")).pack(side="left")

        self.map_fig, self.map_ax = plt.subplots(figsize=(9,8))
        self.map_fig.subplots_adjust(right=0.72)   # reserve space for outside legend
        self.map_ax.set_facecolor("#e8f4f0")
        self.map_ax.set_title("Run calculation to display map", fontsize=10, color="#555")
        self.map_ax.axis("off")

        toolbar_frame = ttk.Frame(right)
        toolbar_frame.grid(row=2, column=0, sticky="ew", padx=6)
        self.map_canvas = FigureCanvasTkAgg(self.map_fig, master=right)
        widget = self.map_canvas.get_tk_widget()
        widget.grid(row=1, column=0, sticky="nsew", padx=6, pady=4)

        # Resize: set figure size in pixels directly, bypassing DPI confusion
        def _on_map_resize(event):
            if event.width > 50 and event.height > 50:
                dpi = self.map_fig.dpi
                self.map_fig.set_size_inches(event.width / dpi, event.height / dpi, forward=False)
                self.map_fig.subplots_adjust(right=0.72)
                self.map_canvas.draw_idle()
        widget.bind("<Configure>", _on_map_resize)

        # Row 2: toolbar
        self.map_toolbar = NavigationToolbar2Tk(self.map_canvas, toolbar_frame)
        self.map_toolbar.update()

        # Row 3: separator
        ttk.Separator(right, orient="horizontal").grid(row=3, column=0, sticky="ew", padx=8, pady=(4, 0))

        # Row 4: logos — fixed height, never squeezed
        logo_inner = tk.Frame(right, bg=MAIN_BG, height=70)
        logo_inner.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 6))
        logo_inner.grid_propagate(False)   # enforce fixed height

        def _load_logo(parent, stem, side):
            for ext in [".png", ".jpg", ".jpeg", ".gif", ".ico"]:
                p = LOGOS_DIR / f"{stem}{ext}"
                if p.exists():
                    try:
                        img   = Image.open(str(p)).convert("RGBA")
                        img.thumbnail((150, 60), Image.Resampling.LANCZOS)
                        photo = ImageTk.PhotoImage(img)
                        lbl   = tk.Label(parent, image=photo, bg=MAIN_BG)
                        lbl.photo = photo
                        lbl.pack(side=side, padx=10, pady=4)
                        return
                    except Exception as exc:
                        print(f"[Logo] Error loading {p}: {exc}")
            text = "Ghent University" if stem == "university" else "Campus Office"
            tk.Label(parent, text=text, bg=MAIN_BG,
                     fg="#016c59", font=("Segoe UI", 10, "bold")).pack(side=side, padx=10, pady=4)

        _load_logo(logo_inner, "university", "left")
        _load_logo(logo_inner, "office",     "right")

    def _browse_file(self, var: tk.StringVar, filetypes):
        fn = filedialog.askopenfilename(filetypes=filetypes)
        if fn:
            var.set(fn)

    def _process_and_export_loss(self):
        base = self.loss_baseline_path.get().strip()
        plan = self.loss_planned_path.get().strip()
        sig  = self.loss_significance.get().strip()

        if not base or not plan:
            messagebox.showerror("Missing files", "Please select both baseline and planned development files.")
            return
        try:
            sig_val = float(sig) if sig else 1.0
        except Exception:
            messagebox.showerror("Invalid significance", "Strategic significance must be numeric.")
            return

        try:
            # Load baseline first so its CRS is available for reference
            shp1 = convert_if_needed(base, is_baseline=True)
            gdf1 = load_and_fix(shp1)

            # CHANGED: Replaced the manual popup/conversion block with your automated helper.
            # This extracts MAPCSASSIGN variables and reprojects automatically.
            gdf2 = handle_planned_development(plan, gdf1)

            print("\n===== DXF DEBUG =====")
            print("DXF CRS:", gdf2.crs)
            print("DXF Bounds:", gdf2.total_bounds)

            # ── Column mapping for baseline shapefile ──────────────────
            missing_cols = [c for c in REQUIRED_LOSS_COLS if c not in gdf1.columns]
            if missing_cols:
                col_map = self._show_column_mapping_dialog(
                    available_cols=list(gdf1.columns),
                    required_cols=missing_cols)
                rename = {v: k for k, v in col_map.items()}
                gdf1 = gdf1.rename(columns=rename)

            if gdf1.crs is None:
                messagebox.showwarning("CRS missing",
                    "Baseline layer has no CRS. Areas may be wrong; ensure a projected CRS is used.")

            

            gdf1 = gdf1[gdf1.geometry.type.isin(["Polygon", "MultiPolygon"])]
            gdf2 = gdf2[gdf2.geometry.type.isin(["Polygon", "MultiPolygon"])]

            if gdf1.empty:
                messagebox.showerror("Error", "Baseline contains no polygons after cleaning.")
                return
            if gdf2.empty:
                messagebox.showerror("Error", "Planned development contains no polygons after cleaning.")
                return

            

            gdf1["area_m2"]   = gdf1.geometry.area
            total_baseline_ha = gdf1["area_m2"].sum() / 10000.0

            def flexible_condition_map(val):
                if pd.isna(val): return np.nan
                s = str(val).strip().lower()
                if s == "good" or ("good" in s and "fairly" not in s): return 3.0
                if "fairly good" in s or "2.5" in s:                    return 2.5
                if "moderate" in s:                                       return 2.0
                if "fairly poor" in s or ("fairly" in s and "poor" in s): return 1.5
                if "poor" in s and "fairly" not in s:                     return 1.0
                return np.nan

            def flexible_distinct_map(val):
                if pd.isna(val): return np.nan
                s = str(val).strip().lower()
                if "v.high" in s or "very high" in s or "8" in s: return 8
                if "high" in s and "very" not in s:                 return 6
                if "medium" in s:                                     return 4
                if "low" in s and "very" not in s:                  return 2
                if "v.low" in s or "very low" in s:                 return 0
                return np.nan

            cond_col  = COL_CONDITION if COL_CONDITION in gdf1.columns else None
            dist_col  = COL_DISTINCT  if COL_DISTINCT  in gdf1.columns else None
            gdf1["Condition score"]       = (gdf1[cond_col] if cond_col else
                pd.Series([np.nan]*len(gdf1))).apply(flexible_condition_map)
            gdf1["Distinctiveness score"] = (gdf1[dist_col] if dist_col else
                pd.Series([np.nan]*len(gdf1))).apply(flexible_distinct_map)
            gdf1["Significance score"]    = sig_val

            # Urban exclusion checkbox (exclude only if column present)
            if COL_BROAD in gdf1.columns:
                gdf1 = gdf1[gdf1[COL_BROAD].astype(str).str.strip().str.lower() != "urban"]

            nan_cond = int(gdf1["Condition score"].isna().sum())
            nan_dist = int(gdf1["Distinctiveness score"].isna().sum())
            if nan_cond > 0 or nan_dist > 0:
                messagebox.showwarning("Mapping issues",
                    f"Some values couldn't be mapped:\n"
                    f"Condition unmapped: {nan_cond}\n"
                    f"Distinctiveness unmapped: {nan_dist}")

            baseline_union = _safe_union(gdf1)
            planned_union = _safe_union(gdf2)

            print("\n===== OVERLAP DEBUG =====")
            print("Intersects:", baseline_union.intersects(planned_union))
            print("Distance:", baseline_union.distance(planned_union))
            intersection = gpd.overlay(gdf1, gdf2, how="intersection", keep_geom_type=True)
            intersection = intersection[intersection.geometry.type.isin(["Polygon", "MultiPolygon"])]
            if intersection.empty:
                # Show diagnostic bounds so user can see why there is no overlap
                b = gdf1.total_bounds  # [minx, miny, maxx, maxy]
                p = gdf2.total_bounds
                messagebox.showerror("No overlap",
                    f"No overlap found between baseline and planned development.\n\n"
                    f"Baseline extent:\n"
                    f"  X: {b[0]:,.0f} → {b[2]:,.0f}\n"
                    f"  Y: {b[1]:,.0f} → {b[3]:,.0f}\n\n"
                    f"Planned development extent:\n"
                    f"  X: {p[0]:,.0f} → {p[2]:,.0f}\n"
                    f"  Y: {p[1]:,.0f} → {p[3]:,.0f}\n\n"
                    f"If these ranges do not overlap, your files are not in the same "
                    f"spatial location. Check CRS and coordinates in QGIS.")
                return

            intersection["Loss area (ha)"]    = (intersection.geometry.area / 10000.0).round(4)
            intersection["Biodiversity units"] = (
                intersection["Loss area (ha)"] *
                intersection.get("Condition score",      0).fillna(0) *
                intersection.get("Significance score",   sig_val).fillna(sig_val) *
                intersection.get("Distinctiveness score", 0).fillna(0)
            ).round(4)

            total_loss_ha  = float(intersection["Loss area (ha)"].sum())
            total_biodiv   = float(intersection["Biodiversity units"].sum())
            summary_lines  = []

            if COL_BROAD in intersection.columns:
                summary_data = []
                for ht in intersection[COL_BROAD].unique():
                    sub = intersection[intersection[COL_BROAD] == ht]
                    summary_data.append({
                        "Habitat Type":      ht,
                        "Area (ha)":         sub["Loss area (ha)"].sum(),
                        "Biodiversity Loss":  sub["Biodiversity units"].sum(),
                    })
                summary_df = pd.DataFrame(summary_data).sort_values("Biodiversity Loss", ascending=False)
                if not summary_df.empty:
                    summary_lines = ["", "Habitat Loss Summary by Habitat Type:", "-" * 45]
                    for _, row in summary_df.iterrows():
                        summary_lines.append(
                            f"{row['Habitat Type']}:  "
                            f"Area = {row['Area (ha)']:.3f} ha,  "
                            f"Biodiversity Loss = {row['Biodiversity Loss']:.3f}"
                        )

            lines = [
                f"Baseline total area (ha): {total_baseline_ha:,.3f}",
                f"Total overlap / loss area (ha): {total_loss_ha:,.3f}",
                f"Total biodiversity units (loss): {total_biodiv:,.3f}",
            ] + summary_lines

            self._last_loss_units = total_biodiv  # store for comparison tab
            self.loss_results_text.delete("1.0", "end")
            self.loss_results_text.insert("end", "\n".join(lines))

            # Draw the embedded static map (only this — no auto-popup or auto-export)
            self._draw_static_map(gdf1, gdf2, intersection)

            # Ask to save shapefile and CSV
            if messagebox.askyesno("Save results", "Save intersection shapefile and CSV of results?"):
                shp_path = filedialog.asksaveasfilename(defaultextension=".shp",
                    filetypes=[("Shapefile", "*.shp")], title="Save intersection shapefile")
                if shp_path:
                    try:
                        intersection.to_file(shp_path)
                        messagebox.showinfo("Saved", f"Shapefile saved to: {shp_path}")
                    except Exception as e:
                        messagebox.showerror("Save error", f"Failed to save shapefile: {e}")

                csv_path = filedialog.asksaveasfilename(defaultextension=".csv",
                    filetypes=[("CSV", "*.csv")], title="Save CSV of loss results")
                if csv_path:
                    try:
                        cols = ["Loss area (ha)", "Condition score", "Distinctiveness score",
                                "Significance score", "Biodiversity units"]
                        intersection[cols].to_csv(csv_path, index=False)
                        messagebox.showinfo("Saved", f"CSV saved to: {csv_path}")
                    except Exception as e:
                        messagebox.showerror("Save error", f"Failed to save CSV: {e}")

        except Exception as ex:
            messagebox.showerror("Processing Error", f"An unexpected error occurred:\n{str(ex)}")

    # -------------------- Static map --------------------
    def _draw_static_map(self, gdf1, gdf2, intersection):
        """Render result map in the embedded matplotlib panel."""
        self._map_gdf1         = gdf1
        self._map_gdf2         = gdf2
        self._map_intersection = intersection

        self.map_ax.clear()
        self.map_ax.set_facecolor("#eaf2ea")

        # alias — defined ONCE here, used everywhere below
        ax = self.map_ax

        # ── Reproject to Lambert 72 ──────────────────────────────────
        def safe_reproject(gdf):
            try:
                if gdf.crs is not None:
                    return gdf.to_crs(epsg=31370)
            except Exception:
                pass
            return gdf

        g1    = safe_reproject(gdf1)
        g2    = safe_reproject(gdf2)
        inter = safe_reproject(intersection)

        # ── Color palette ────────────────────────────────────────────
        BASELINE_COLOR = "#b2d8b2"
        LOSS_PALETTE   = [
            "#ffffcc",  # yellow
            "#006837",  # dark green
            "#993404",  # dark orange
            "#fbb4b9",  # pinkish
            "#980043",  # maroon
            "#17becf",  # teal
            "#bcbd22",  # olive
            "#1f77b4",  # blue
        ]

        loss_col = ("Baseline Broad Habitat Type"
                    if "Baseline Broad Habitat Type" in inter.columns else None)

        # ── Layer 1: Baseline ────────────────────────────────────────
        g1.plot(ax=ax, color=BASELINE_COLOR, alpha=0.45,
                linewidth=0.6, edgecolor="#5a8a5a")

        # ── Layer 2: Planned development ─────────────────────────────
        g2.plot(ax=ax, facecolor="none", edgecolor="#222222",
                linewidth=0.9, linestyle="--")

        # ── Layer 3: Loss areas (one color per habitat type) ─────────
        loss_types  = []
        loss_colors = {}
        if loss_col and not inter.empty:
            loss_types  = list(inter[loss_col].dropna().unique())
            loss_colors = {h: LOSS_PALETTE[i % len(LOSS_PALETTE)]
                           for i, h in enumerate(loss_types)}
            for habitat, color in loss_colors.items():
                inter[inter[loss_col] == habitat].plot(
                    ax=ax, color=color, alpha=0.90,
                    linewidth=0.5, edgecolor="#333333")
        else:
            inter.plot(ax=ax, color=LOSS_PALETTE[0], alpha=0.85,
                       linewidth=0.5, edgecolor="#333333")

        # ── Legend (clean labels, outside axes to the right) ─────────
        clean_patches = [
            mpatches.Patch(facecolor=BASELINE_COLOR, edgecolor="#5a8a5a",
                           alpha=0.6, label="Baseline habitat"),
            mpatches.Patch(facecolor="none", edgecolor="#222222",
                           linewidth=1.4, linestyle="--", label="Planned development"),
        ]
        if loss_types:
            for habitat in loss_types:
                clean_patches.append(
                    mpatches.Patch(color=loss_colors[habitat], alpha=0.90, label=habitat))
        else:
            clean_patches.append(
                mpatches.Patch(color=LOSS_PALETTE[0], alpha=0.85, label="Intersection"))

        legend = ax.legend(
            handles=clean_patches,
            loc="upper left",
            bbox_to_anchor=(1.01, 1.0),
            borderaxespad=0,
            fontsize=5,
            title="Legend",
            title_fontsize=6,
            framealpha=0.95,
            edgecolor="#aaaaaa",
            fancybox=False,
            borderpad=0.7,
            labelspacing=0.4,
            handlelength=1.3,
            handleheight=1.0,
            handletextpad=0.5,
        )
        legend.get_frame().set_linewidth(0.6)

        # ── North arrow (lower-right, axes-fraction coords) ──────────
        ax.annotate("",
            xy=(0.96, 0.98), xytext=(0.96, 0.86),
            xycoords="axes fraction", textcoords="axes fraction",
            arrowprops=dict(arrowstyle="-|>", color="black", lw=1.4, mutation_scale=10))
        

        # ── Scale bar (Lambert 72 — units already in metres) ─────────
        xlim         = ax.get_xlim()
        ylim         = ax.get_ylim()
        map_width_m  = xlim[1] - xlim[0]
        map_height_m = ylim[1] - ylim[0]

        target_m = map_width_m * 0.15
        scale_m  = 10   # safe fallback
        for step in [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500,
                     1000, 2000, 2500, 5000, 10000]:
            if step >= target_m * 0.5:
                scale_m = step
                break

        scale_bar_len = scale_m   # metres == coordinate units, no conversion needed

        bar_x0 = xlim[1] - map_width_m  * 0.08 - scale_bar_len
        bar_y0 = ylim[0] + map_height_m * 0.04
        bar_y1 = bar_y0  + map_height_m * 0.012
        half   = scale_bar_len / 2

        # Clean standard alternating segments
        segments = [
            (bar_x0, bar_x0 + half, "black"),
            (bar_x0 + half, bar_x0 + scale_bar_len, "white")
        ]

        for x_start, x_end, fc in segments:
            ax.fill_between([x_start, x_end], bar_y0, bar_y1, color=fc, transform=ax.transData, zorder=5)
            ax.plot([x_start, x_end, x_end, x_start, x_start],
                    [bar_y0, bar_y0, bar_y1, bar_y1, bar_y0],
                    color="black", linewidth=0.5, transform=ax.transData, zorder=6)

        # Dynamic label selection
        label_m = f"{scale_m} m" if scale_m < 1000 else f"{scale_m / 1000.0:.1f} km"
        text_padding = map_height_m * 0.018

        # 0 is pushed slightly left to stay out of the way
        ax.text(bar_x0, bar_y1 + text_padding, "0", 
                ha="right", va="bottom", fontsize=5, color="black", zorder=7)
        
        # The middle number (e.g. 50) is pushed slightly left of the center joint
        mid_label = str(int(scale_m / 2)) if scale_m >= 2 else ""
        if mid_label:
          ax.text(bar_x0 + half, bar_y1 + text_padding, mid_label,
            ha="center", va="bottom", fontsize=5, color="black", zorder=7)
        
        # The total length (e.g. 100 m) is pushed right so it never overlaps the middle label
        ax.text(bar_x0 + scale_bar_len-(scale_bar_len * 0.004), bar_y1 + text_padding, label_m, 
                ha="left", va="bottom", fontsize=5, color="black", zorder=7)

        # ── Titles and axis labels ────────────────────────────────────
        ax.set_title("Biodiversity Loss per Habitat", fontsize=10, pad=8, color="#1a1a1a")
    
        ax.tick_params(axis='y', labelsize=4.5)
        ax.tick_params(axis='x', labelsize=4.5, labelrotation=90)
        
        # Fix: Dropped coordinate text transformation from -0.3 to -0.45 to prevent Easting overlap
        ax.text(
            0.0, -0.17, 
            "Coordinate system: Belgian Lambert 72, EPSG:31370", 
            fontsize=5,      
            color='#666666',   
            transform=ax.transAxes, 
            ha='left', 
            va='top'
        )

        # Use actual widget pixel size for the figure
        w = self.map_canvas.get_tk_widget().winfo_width()
        h = self.map_canvas.get_tk_widget().winfo_height()
        if w > 50 and h > 50:
            self.map_fig.set_size_inches(w / self.map_fig.dpi,
                                         h / self.map_fig.dpi, forward=False)
        
        # Increased bottom margin allocation from 0.08 to 0.18 to ensure CRS text stays visible
        self.map_fig.subplots_adjust(right=0.72, left=0.10, top=0.93, bottom=0.18)
        self.map_canvas.draw()

    # -------------------- Interactive map (Folium) --------------------
    def _show_interactive_map(self):
        if self._map_intersection is None:
            messagebox.showwarning("No data", "Run the calculation first.")
            return

        try:
            def safe_to_wgs84(gdf):
                """Reproject to WGS84, re-validating geometries before and after
                to prevent TopologyException from tiny invalidity exposed by reprojection."""
                try:
                    # Clean before reprojecting
                    gdf = gdf.copy()
                    gdf["geometry"] = gdf.geometry.buffer(0)
                    gdf["geometry"] = gdf.geometry.apply(make_valid)
                    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
                    if gdf.crs is not None:
                        gdf = gdf.to_crs(epsg=4326)
                    # Clean again after reprojecting
                    gdf["geometry"] = gdf.geometry.buffer(0)
                    gdf["geometry"] = gdf.geometry.apply(make_valid)
                    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
                except Exception:
                    pass
                return gdf

            g1    = safe_to_wgs84(self._map_gdf1)
            g2    = safe_to_wgs84(self._map_gdf2)
            inter = safe_to_wgs84(self._map_intersection)

            # union_all() requires geopandas >= 0.14; fall back for older versions
            try:
                combined = g1.geometry.union_all()
            except AttributeError:
                combined = g1.geometry.unary_union

            centroid = combined.centroid
            m = folium.Map(location=[centroid.y, centroid.x], zoom_start=15, tiles="CartoDB positron")

            habitat_col = ("Baseline Broad Habitat Type"
                           if "Baseline Broad Habitat Type" in g1.columns else None)
            palette = ["#74c476", "#41ab5d", "#238b45", "#006d2c", "#00441b",
                       "#9ecae1", "#6baed6", "#3182bd", "#08519c", "#fdae6b"]

            if habitat_col:
                habitat_types = g1[habitat_col].dropna().unique()
                color_map = {h: palette[i % len(palette)] for i, h in enumerate(habitat_types)}
                for habitat in habitat_types:
                    subset = g1[g1[habitat_col] == habitat]
                    folium.GeoJson(
                        subset.__geo_interface__,
                        name=f"Baseline: {habitat}",
                        style_function=lambda f, c=color_map[habitat]: {
                            "fillColor": c, "color": "#666", "weight": 0.8, "fillOpacity": 0.3},
                        tooltip=folium.GeoJsonTooltip(fields=[habitat_col])
                    ).add_to(m)
            else:
                folium.GeoJson(g1.__geo_interface__, name="Baseline",
                    style_function=lambda f: {"fillColor": "#74c476", "color": "#555",
                                              "weight": 0.8, "fillOpacity": 0.3}).add_to(m)

            folium.GeoJson(g2.__geo_interface__, name="Planned Development",
                style_function=lambda f: {"fillColor": "none", "color": "#e63946",
                                          "weight": 2.5, "dashArray": "6,4", "fillOpacity": 0}
            ).add_to(m)

            loss_col = ("Baseline Broad Habitat Type"
                        if "Baseline Broad Habitat Type" in inter.columns else None)
            if loss_col:
                loss_types  = inter[loss_col].dropna().unique()
                loss_colors = {h: f"#{hex(max(80, 200 - 30 * i))[2:].zfill(2)}2020"
                               for i, h in enumerate(loss_types)}
                tt_fields = [f for f in [loss_col, "Loss area (ha)", "Biodiversity units"]
                             if f in inter.columns]
                for habitat in loss_types:
                    subset = inter[inter[loss_col] == habitat]
                    folium.GeoJson(
                        subset.__geo_interface__,
                        name=habitat,
                        style_function=lambda f, c=loss_colors.get(habitat, "#e63946"): {
                            "fillColor": c, "color": "#800", "weight": 0.6, "fillOpacity": 0.8},
                        tooltip=folium.GeoJsonTooltip(fields=tt_fields) if tt_fields else None
                    ).add_to(m)
            else:
                folium.GeoJson(inter.__geo_interface__, name="Loss areas",
                    style_function=lambda f: {"fillColor": "#e63946", "color": "#800",
                                              "weight": 0.6, "fillOpacity": 0.8}).add_to(m)

            folium.LayerControl(collapsed=False).add_to(m)

            # Save to user home folder (always writable)
            tmp_dir  = Path(os.path.expanduser("~")) / "BiodiversityTool_maps"
            tmp_dir.mkdir(exist_ok=True)
            tmp_path = tmp_dir / f"map_{int(time.time())}.html"
            m.save(str(tmp_path))
            # Keep only 5 most recent maps to avoid accumulation
            existing = sorted(tmp_dir.glob("map_*.html"))
            for old_f in existing[:-4]:
                try: old_f.unlink()
                except Exception: pass

            # Try to open browser, always show path so user can open manually if needed
            try:
                webbrowser.open(tmp_path.as_uri())
                messagebox.showinfo("Interactive Map",
                    f"Map opened in your browser.\n\n"
                    f"If it did not open, paste this path into your browser:\n{tmp_path}")
            except Exception:
                messagebox.showinfo("Interactive Map",
                    f"Could not open browser automatically.\n\n"
                    f"Open this file manually in your browser:\n{tmp_path}")

        except Exception as e:
            messagebox.showerror("Map Error",
                f"Failed to generate interactive map:\n\n{str(e)}\n\n"
                f"Make sure folium is installed:  pip install folium branca")

    # -------------------- Export static map --------------------
    def _export_static_map(self):
        if self._map_intersection is None:
            messagebox.showwarning("No data", "Run the calculation first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("PDF file", "*.pdf"), ("SVG file", "*.svg")],
            title="Export map as...")
        if path:
            try:
                self.map_fig.savefig(path, dpi=200, bbox_inches="tight")
                messagebox.showinfo("Saved", f"Map exported to:\n{path}")
            except Exception as e:
                messagebox.showerror("Export failed", str(e))

    # -------------------- Gain Tab --------------------
    def _build_gain_tab(self):
        card = ttk.Frame(self.tab_gain, padding=12, relief="raised")
        card.pack(fill="both", expand=True, padx=10, pady=8)

        ttk.Separator(card, orient="horizontal").pack(fill="x", pady=5)
        frm = ttk.Frame(card)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Parameter",        font=("Segoe UI", 10, "bold")).grid(row=0, column=0, padx=8, pady=6, sticky="w")
        ttk.Label(frm, text="Explanation",      font=("Segoe UI", 10, "bold")).grid(row=0, column=1, padx=8, pady=6, sticky="w")
        ttk.Label(frm, text="Selection",        font=("Segoe UI", 10, "bold")).grid(row=0, column=2, padx=8, pady=6, sticky="w")
        ttk.Label(frm, text="Multiplier / Score", font=("Segoe UI", 10, "bold")).grid(row=0, column=3, padx=8, pady=6, sticky="w")

        self.var_code      = tk.StringVar()
        self.var_broad     = tk.StringVar()
        self.var_specific  = tk.StringVar()
        self.var_year      = tk.StringVar()
        self.var_condition = tk.StringVar()
        self.var_difficulty= tk.StringVar()
        self.var_spatial   = tk.StringVar()
        self.var_strategic = tk.StringVar()
        self.var_area      = tk.StringVar()

        r = 1
        def add_row(label_text, explanation_text, widget):
            nonlocal r
            ttk.Label(frm, text=label_text).grid(row=r, column=0, sticky="w", padx=8, pady=(6, 2))
            ttk.Label(frm, text=explanation_text, font=("Segoe UI", 9, "italic"),
                      foreground="#444").grid(row=r, column=1, sticky="w", padx=8, pady=(6, 2))
            widget.grid(row=r, column=2, sticky="w", padx=8, pady=(6, 2))
            this_row = r
            r += 1
            return this_row

        code_ent = ttk.Entry(frm, textvariable=self.var_code, width=18)
        row_p = add_row("Plot code:", "Enter plot code.", code_ent)

        broad_vals = sorted(self.habitats_df["Broad Habitat Type"].unique().tolist())
        cb_broad   = AutocompleteCombobox(frm, textvariable=self.var_broad, state="normal", width=44)
        cb_broad.set_completion_list(broad_vals)
        row_b = add_row("Broad habitat", "Major habitat type", cb_broad)

        cb_specific = AutocompleteCombobox(frm, textvariable=self.var_specific, state="normal", width=44)
        cb_specific.set_completion_list([])
        row_s = add_row("Specific Habitat:", "Detailed habitat type (filtered by broad type).", cb_specific)

        year_vals = [str(x) for x in self.years_df["Years"].tolist()]
        cb_year   = AutocompleteCombobox(frm, textvariable=self.var_year, values=year_vals, state="normal", width=20)
        row_y = add_row("Time to target (years):", "How long until habitat reaches target ecological value.", cb_year)

        cb_condition = ttk.Combobox(frm, textvariable=self.var_condition,
            values=list(CONDITION_MAPPING.keys()), state="readonly", width=28)
        row_c = add_row("Habitat condition:", "The current quality and health of the habitat.", cb_condition)

        cb_difficulty = ttk.Combobox(frm, textvariable=self.var_difficulty,
            values=list(DIFFICULTY_MAPPING.keys()), state="readonly", width=28)
        row_d = add_row("Difficulty category:", "Uncertainty in effectiveness of compensation techniques.", cb_difficulty)

        cb_spatial = ttk.Combobox(frm, textvariable=self.var_spatial,
            values=list(SPATIAL_MAPPING.keys()), state="readonly", width=28)
        row_sp = add_row("Spatial risk category:", "Location risk for habitat creation (closer is better).", cb_spatial)

        ent_strategic = ttk.Combobox(frm, textvariable=self.var_strategic,
            values=list(STRATEGIC_MAPPING.keys()), state="readonly", width=18)
        row_st = add_row("Strategic significance:",
            "High: Important for achieving goals of biodiversity plan = 1.15.  Low: 1.0", ent_strategic)

        ent_area = ttk.Entry(frm, textvariable=self.var_area, width=18)
        row_a = add_row("Area (ha):", "Enter parcel area in hectares (e.g., 2.5).", ent_area)

        self.lbl_code     = ttk.Label(frm, text="Plot code: -");         self.lbl_code.grid(row=row_p,  column=3, sticky="w", padx=6)
        self.lbl_distinct = ttk.Label(frm, text="Distinctiveness: -");   self.lbl_distinct.grid(row=row_s, column=3, sticky="w", padx=6)
        self.lbl_yearmult = ttk.Label(frm, text="Year multiplier: -");   self.lbl_yearmult.grid(row=row_y, column=3, sticky="w", padx=6)
        self.lbl_cond     = ttk.Label(frm, text="Condition score: -");   self.lbl_cond.grid(row=row_c,  column=3, sticky="w", padx=6)
        self.lbl_diff     = ttk.Label(frm, text="Difficulty multiplier: -"); self.lbl_diff.grid(row=row_d, column=3, sticky="w", padx=6)
        self.lbl_spat     = ttk.Label(frm, text="Spatial multiplier: -"); self.lbl_spat.grid(row=row_sp, column=3, sticky="w", padx=6)
        self.lbl_strat    = ttk.Label(frm, text="Strategic multiplier: -"); self.lbl_strat.grid(row=row_st, column=3, sticky="w", padx=6)
        self.lbl_area     = ttk.Label(frm, text="Area: -");               self.lbl_area.grid(row=row_a,  column=3, sticky="w", padx=6)

        btn_frame = ttk.Frame(card)
        btn_frame.pack(fill="x", pady=10)
        ttk.Button(btn_frame, text="Calculate",                                    command=self._calculate_gain).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Add to List",                                  command=self._add_gain_to_list).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Clear List",                                   command=self._clear_gain_list).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Save selection (CSV & saved results)",         command=self._save_gain_selection).pack(side="left", padx=8)

        ttk.Separator(card, orient="horizontal").pack(fill="x", pady=10)
        self.gain_result = ttk.Label(card, text="Biodiversity Units: ", font=("Segoe UI", 12, "bold"))
        self.gain_result.pack(anchor="w", pady=(6, 0), padx=6)
        self.gain_total_label = ttk.Label(card, text="Total Biodiversity Units: 0.000",
            font=("Segoe UI", 12, "bold"), foreground="#2E7D32")
        self.gain_total_label.pack(anchor="w", pady=(10, 5))

        ttk.Separator(card, orient="horizontal").pack(fill="x", pady=5)
        self.equation = ttk.Label(card,
            text="Biodiversity Units = Distinctiveness × Year multiplier × Condition × Difficulty × Spatial risk × Strategic Significance × Area")
        self.equation.pack(anchor="w", pady=(6, 0), padx=6)

        ttk.Separator(card, orient="horizontal").pack(fill="x", pady=10)
        list_frame = ttk.Label(card, text="Saved Gain Calculations", padding=5)
        list_frame.pack(fill="both", expand=True, pady=10)

        columns = ("Plot code", "Habitat", "Area (ha)", "Units", "Delete")
        self.gain_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=6)
        for col in ["Plot code", "Habitat", "Area (ha)", "Units"]:
            self.gain_tree.heading(col, text=col)
        self.gain_tree.heading("Delete", text="")
        self.gain_tree.column("Delete", width=50)
        self.gain_tree.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.gain_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.gain_tree.configure(yscrollcommand=scrollbar.set)
        self.gain_tree.bind("<Double-1>", self._remove_gain_item)

        code_ent.bind("<KeyRelease>",           lambda e: self._on_code_change())
        cb_broad.bind("<<ComboboxSelected>>",   lambda e: self._on_broad_change(cb_specific))
        cb_broad.bind("<KeyRelease>",            lambda e: self._on_broad_change(cb_specific))
        cb_specific.bind("<<ComboboxSelected>>",lambda e: self._on_specific_change())
        cb_year.bind("<<ComboboxSelected>>",    lambda e: self._on_year_change())
        cb_condition.bind("<<ComboboxSelected>>",lambda e: self._on_condition_change())
        cb_difficulty.bind("<<ComboboxSelected>>",lambda e: self._on_difficulty_change())
        cb_spatial.bind("<<ComboboxSelected>>", lambda e: self._on_spatial_change())
        ent_strategic.bind("<<ComboboxSelected>>",lambda e: self._on_strategic_change())
        ent_area.bind("<KeyRelease>",           lambda e: self._on_area_change())

    # -------------------- Baseline Tab --------------------
    def _build_baseline_tab(self):
        outer  = ttk.Frame(self.tab_baseline)
        outer.pack(fill="both", expand=True)

        canvas    = tk.Canvas(outer, bg=MAIN_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        card        = ttk.Frame(canvas, padding=12)
        card_window = canvas.create_window((0, 0), window=card, anchor="nw")

        card.bind("<Configure>",   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(card_window, width=e.width))
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        upload_frame = ttk.LabelFrame(card, text="📂 Upload Baseline Data", padding=10)
        upload_frame.pack(fill="x", pady=(0, 10))
        file_frame = ttk.Frame(upload_frame)
        file_frame.pack(fill="x", pady=5)
        ttk.Label(file_frame, text="Baseline file (CSV/Excel):").pack(side="left", padx=(0, 10))
        self.baseline_file_path = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.baseline_file_path, width=50).pack(side="left", padx=(0, 5))
        ttk.Button(file_frame, text="Browse", command=self._browse_baseline_file).pack(side="left", padx=2)
        ttk.Button(file_frame, text="Load",   command=self._load_baseline_file).pack(side="left", padx=2)

        parcel_frame  = ttk.LabelFrame(card, text="📋  Select Parcel", padding=10)
        parcel_frame.pack(fill="x", pady=(0, 10))
        select_frame  = ttk.Frame(parcel_frame)
        select_frame.pack(fill="x")
        ttk.Label(select_frame, text="Parcel Reference:").pack(side="left", padx=(0, 10))
        self.parcel_var   = tk.StringVar()
        self.parcel_combo = ttk.Combobox(select_frame, textvariable=self.parcel_var, state="readonly", width=20)
        self.parcel_combo.pack(side="left", padx=(0, 10))
        self.parcel_combo.bind("<<ComboboxSelected>>", self._on_parcel_selected_baseline)
        ttk.Button(select_frame, text="Clear", command=self._clear_baseline_data).pack(side="left")

        self.details_frame         = ttk.LabelFrame(card, text="📍 Parcel Details & Mapping", padding=10)
        self.baseline_result_frame = ttk.LabelFrame(card, text="📊  Baseline Biodiversity Units", padding=10)
        self.comparison_frame      = ttk.LabelFrame(card, text="🔄  Compare with Gain Calculator", padding=10)

    def _browse_baseline_file(self):
        fn = filedialog.askopenfilename(filetypes=[
            ("CSV files", "*.csv"), ("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
        if fn:
            self.baseline_file_path.set(fn)

    def _load_baseline_file(self):
        filepath = self.baseline_file_path.get().strip()
        if not filepath:
            messagebox.showerror("Error", "Please select a file first")
            return
        try:
            self.baseline_df = (pd.read_csv(filepath) if filepath.endswith(".csv")
                                else pd.read_excel(filepath))
            # Column mapping — show dialog for any missing expected columns
            missing = [c for c in REQUIRED_BASE_COLS if c not in self.baseline_df.columns]
            if missing:
                col_map = self._show_column_mapping_dialog(
                    available_cols=list(self.baseline_df.columns),
                    required_cols=missing)
                rename = {v: k for k, v in col_map.items()}
                self.baseline_df = self.baseline_df.rename(columns=rename)
                # Check again after mapping
                still_missing = [c for c in REQUIRED_BASE_COLS
                                 if c not in self.baseline_df.columns]
                if still_missing:
                    messagebox.showwarning("Columns skipped",
                        f"These columns were not mapped and will be unavailable:\n"
                        + "\n".join(still_missing))
            self.parcel_combo["values"] = self.baseline_df[COL_PARCEL].astype(str).tolist()
            messagebox.showinfo("Success", f"Loaded {len(self.baseline_df)} parcels")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file:\n{str(e)}")
            self.baseline_df = None

    def _on_parcel_selected_baseline(self, event):
        parcel_ref = self.parcel_var.get()
        if self.baseline_df is None:
            return

        match = self.baseline_df[self.baseline_df[COL_PARCEL].astype(str) == parcel_ref]
        if match.empty:
           messagebox.showerror("Error", f"Parcel '{parcel_ref}' not found."); return
        self.current_parcel_data = match.iloc[0]

        for widget in self.details_frame.winfo_children():
            widget.destroy()
        self.details_frame.pack(fill="x", pady=(0, 10))

        broad_habitat  = self.current_parcel_data[COL_BROAD]
        specific_habitat = self.current_parcel_data[COL_HABITAT]
        try:
            area_m2 = float(self.current_parcel_data[COL_AREA])
        except (ValueError, TypeError):
            messagebox.showerror("Error", f"Area value is not numeric for parcel {parcel_ref}")
            return
        area_ha        = area_m2 / 10000
        condition_text = self.current_parcel_data[COL_CONDITION]
        if ". " in str(condition_text):
            condition_text = str(condition_text).split(". ")[1]
        strategic_text = self.current_parcel_data.get(COL_STRATEGIC, "Unknown")

        ttk.Label(self.details_frame, text=f"Parcel: {parcel_ref}",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(self.details_frame, text="Broad Habitat:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(self.details_frame, text=broad_habitat).grid(row=1, column=1, sticky="w", padx=5, pady=2)
        self.broad_mapping_var   = tk.StringVar()
        self.broad_mapping_combo = ttk.Combobox(self.details_frame, textvariable=self.broad_mapping_var,
                                                state="readonly", width=35)
        self.broad_mapping_combo["values"] = sorted(self.habitats_df["Broad Habitat Type"].unique().tolist())
        self.broad_mapping_combo.grid(row=1, column=2, sticky="w", padx=5, pady=2)
        self.broad_mapping_combo.bind("<<ComboboxSelected>>", self._on_broad_mapping_change)

        ttk.Label(self.details_frame, text="Specific Habitat:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(self.details_frame, text=specific_habitat).grid(row=2, column=1, sticky="w", padx=5, pady=2)
        self.specific_mapping_var   = tk.StringVar()
        self.specific_mapping_combo = ttk.Combobox(self.details_frame, textvariable=self.specific_mapping_var,
                                                   state="readonly", width=35)
        self.specific_mapping_combo["values"] = (
            self.habitats_df[self.habitats_df["Broad Habitat Type"] == broad_habitat]["Specific Habitat"].tolist())
        self.specific_mapping_combo.grid(row=2, column=2, sticky="w", padx=5, pady=2)

        ttk.Label(self.details_frame, text="Area:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(self.details_frame,
                  text=f"{area_m2:,.0f} m² = {area_ha:.4f} ha").grid(row=3, column=1, sticky="w", padx=5, pady=2)

        condition_score = next(
            (v for k, v in CONDITION_MAPPING.items()
             if k.lower() == str(condition_text).strip().lower()), 0)
        ttk.Label(self.details_frame, text="Condition:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(self.details_frame,
                  text=f"{condition_text} (Score: {condition_score})").grid(row=4, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(self.details_frame, text="Strategic Significance:").grid(row=5, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(self.details_frame, text=strategic_text).grid(row=5, column=1, sticky="w", padx=5, pady=2)
        self.strategic_mapping_var   = tk.StringVar()
        self.strategic_mapping_combo = ttk.Combobox(self.details_frame, textvariable=self.strategic_mapping_var,
                                                    state="readonly", width=20)
        self.strategic_mapping_combo["values"] = ["High", "Medium", "Low"]
        self.strategic_mapping_combo.grid(row=5, column=2, sticky="w", padx=5, pady=2)

        ttk.Button(self.details_frame, text="✅ Calculate Baseline Units",
                   command=self._calculate_baseline_units).grid(row=6, column=0, columnspan=3, pady=15)

        self.baseline_area_ha        = area_ha
        self.baseline_condition_text = condition_text
        self.baseline_condition_score = condition_score
        self.baseline_broad_habitat  = broad_habitat
        self.baseline_specific_habitat = specific_habitat

    def _on_broad_mapping_change(self, event):
        selected_broad = self.broad_mapping_var.get()
        if selected_broad:
            self.specific_mapping_combo["values"] = (
                self.habitats_df[self.habitats_df["Broad Habitat Type"] == selected_broad]
                ["Specific Habitat"].tolist())
            self.specific_mapping_var.set("")

    def _calculate_baseline_units(self):
        mapped_broad     = self.broad_mapping_var.get()
        mapped_specific  = self.specific_mapping_var.get()
        mapped_strategic = self.strategic_mapping_var.get()

        if not mapped_broad:
            messagebox.showwarning("Warning", "Please map the broad habitat first"); return
        if not mapped_specific:
            messagebox.showwarning("Warning", "Please map the specific habitat first"); return
        if not mapped_strategic:
            messagebox.showwarning("Warning", "Please map the strategic significance first"); return

        row_check = self.habitats_df[
            (self.habitats_df["Broad Habitat Type"] == mapped_broad) &
            (self.habitats_df["Specific Habitat"]   == mapped_specific)]
        if row_check.empty:
            messagebox.showerror("Error",
                f"Habitat '{mapped_specific}' does not belong to broad habitat '{mapped_broad}'"); return

        row = self.habitats_df[self.habitats_df["Specific Habitat"] == mapped_specific]
        if row.empty:
            messagebox.showerror("Error", f"Habitat '{mapped_specific}' not found in database"); return

        distinctiveness_score = float(row.iloc[0].get("Distinctiveness Score", 0.0))
        strategic_multiplier  = STRATEGIC_MAPPING.get(mapped_strategic, 1.0)
        baseline_units        = (self.baseline_area_ha * self.baseline_condition_score *
                                 strategic_multiplier * distinctiveness_score)

        for widget in self.baseline_result_frame.winfo_children():
            widget.destroy()
        self.baseline_result_frame.pack(fill="x", pady=(0, 10))

        result_text = (
            f"\nBaseline Biodiversity Units: {baseline_units:.4f}\n\n"
            f"Calculation:\n"
            f"  Area: {self.baseline_area_ha:.4f} ha\n"
            f"  Condition: {self.baseline_condition_text} (Score: {self.baseline_condition_score})\n"
            f"  Strategic Significance: {mapped_strategic} (Multiplier: {strategic_multiplier})\n"
            f"  Distinctiveness: {mapped_specific} (Score: {distinctiveness_score})\n\n"
            f"Formula: Area × Condition × Strategic × Distinctiveness"
        )
        ttk.Label(self.baseline_result_frame, text=result_text,
                  font=("Segoe UI", 10), justify="left").pack(anchor="w", padx=10, pady=10)

        self.baseline_units              = baseline_units
        self.mapped_broad_for_comparison = mapped_broad
        self.mapped_specific_for_comparison = mapped_specific
        self._show_comparison_section()

    def _show_comparison_section(self):
        for widget in self.comparison_frame.winfo_children():
            widget.destroy()
        self.comparison_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(self.comparison_frame, text=f"Baseline Units: {self.baseline_units:.4f}",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=5)
        ttk.Label(self.comparison_frame,
                  text="Go to 'Gain Calculator' tab to create a scenario, then come back to compare."
                  ).pack(anchor="w", padx=10, pady=5)
        ttk.Button(self.comparison_frame, text="🔄 Compare with current gain scenario",
                   command=self._compare_with_gain_calculator).pack(pady=10)

    def _compare_with_gain_calculator(self):
        gain_tab_result = self.gain_result.cget("text")
        if "Biodiversity Units:" not in gain_tab_result or gain_tab_result == "Biodiversity Units: ---":
            messagebox.showwarning("Warning", "Please calculate a scenario in the Gain Calculator first")
            return
        current_units = float(gain_tab_result.split(":")[1].strip())
        new_baseline  = current_units + self.baseline_units
        messagebox.showinfo("Comparison Result",
            f"📊 BASELINE UNITS: {self.baseline_units:.4f}\n\n"
            f"🆕 GAINED UNITS: {current_units:.4f}\n\n"
            f"▲ NEW BASELINE UNITS = {abs(new_baseline):.4f} units\n\n"
            f"Formula used:\n"
            f"  Baseline: Area × Condition × Strategic × Distinctiveness\n"
            f"  Gain: Area × Condition × Difficulty × Spatial × Strategic × Years × Distinctiveness")

    def _clear_baseline_data(self):
        self.baseline_df         = None
        self.current_parcel_data = None
        self.parcel_var.set("")
        self.parcel_combo["values"] = []
        self.baseline_file_path.set("")
        for attr in ["broad_mapping_var", "specific_mapping_var", "strategic_mapping_var"]:
            if hasattr(self, attr):
                getattr(self, attr).set("")
        for frame in [self.details_frame, self.baseline_result_frame, self.comparison_frame]:
            for widget in frame.winfo_children():
                widget.destroy()
            frame.pack_forget()
        messagebox.showinfo("Cleared", "Baseline data cleared")

    # -------------------- Gain callbacks --------------------
    def _on_code_change(self):
        self.lbl_code.config(text=f"Plot code: {self.var_code.get()}")
        self.gain_result.config(text="Biodiversity Units: -")

    def _on_broad_change(self, cb_specific):
        b = self.var_broad.get()
        related = (self.habitats_df[self.habitats_df["Broad Habitat Type"] == b]
                   ["Specific Habitat"].dropna().unique().tolist())
        cb_specific.set_completion_list(related)
        self.var_specific.set("")
        # Show how many specific habitats are available under this broad type
        if b and related:
            self.lbl_distinct.config(text=f"Distinctiveness: ({len(related)} habitats available)")
        else:
            self.lbl_distinct.config(text="Distinctiveness: -")
        self.gain_result.config(text="Biodiversity Units: -")

    def _on_specific_change(self):
        row = self.habitats_df[self.habitats_df["Specific Habitat"] == self.var_specific.get()]
        if not row.empty:
            self.lbl_distinct.config(text=f"Distinctiveness: {float(row.iloc[0].get('Distinctiveness Score', 0.0))}")
        else:
            self.lbl_distinct.config(text="Distinctiveness: -")
        self.gain_result.config(text="Biodiversity Units: -")

    def _on_year_change(self):
        row = self.years_df[self.years_df["Years"].astype(str) == str(self.var_year.get())]
        self.lbl_yearmult.config(text=f"Year multiplier: {float(row.iloc[0]['Multiplier'])}" if not row.empty else "Year multiplier: -")
        self.gain_result.config(text="Biodiversity Units: -")

    def _on_condition_change(self):
        self.lbl_cond.config(text=f"Condition score: {CONDITION_MAPPING.get(self.var_condition.get(), '-')}")
        self.gain_result.config(text="Biodiversity Units: -")

    def _on_difficulty_change(self):
        self.lbl_diff.config(text=f"Difficulty multiplier: {DIFFICULTY_MAPPING.get(self.var_difficulty.get(), '-')}")
        self.gain_result.config(text="Biodiversity Units: -")

    def _on_spatial_change(self):
        self.lbl_spat.config(text=f"Spatial multiplier: {SPATIAL_MAPPING.get(self.var_spatial.get(), '-')}")
        self.gain_result.config(text="Biodiversity Units: -")

    def _on_strategic_change(self):
        self.lbl_strat.config(text=f"Strategic multiplier: {STRATEGIC_MAPPING.get(self.var_strategic.get(), '-')}")
        self.gain_result.config(text="Biodiversity Units: -")

    def _on_area_change(self):
        self.lbl_area.config(text=f"Area: {self.var_area.get()}")
        self.gain_result.config(text="Biodiversity Units: -")

    def _calculate_gain(self):
        missing = []
        if not self.var_broad.get():     missing.append("Broad habitat")
        if not self.var_specific.get():  missing.append("Specific habitat")
        if not self.var_year.get():      missing.append("Target year")
        if not self.var_condition.get(): missing.append("Condition")
        if not self.var_difficulty.get():missing.append("Difficulty")
        if not self.var_spatial.get():   missing.append("Spatial risk")
        if not self.var_strategic.get(): missing.append("Strategic significance")
        try:
            area = float(self.var_area.get())
            if area <= 0:
                missing.append("Area (positive number)")
        except Exception:
            missing.append("Area (positive number)")
        if missing:
            messagebox.showwarning("Missing fields", "Please complete: " + ", ".join(missing))
            return

        row      = self.habitats_df[self.habitats_df["Specific Habitat"] == self.var_specific.get()]
        distinct = float(row.iloc[0].get("Distinctiveness Score", 0.0)) if not row.empty else 0.0
        rowy     = self.years_df[self.years_df["Years"].astype(str) == str(self.var_year.get())]
        year_mult= float(rowy.iloc[0]["Multiplier"]) if not rowy.empty else 1.0
        cond     = float(CONDITION_MAPPING.get(self.var_condition.get(), 0.0))
        diff     = float(DIFFICULTY_MAPPING.get(self.var_difficulty.get(), 0.0))
        spat     = float(SPATIAL_MAPPING.get(self.var_spatial.get(), 0.0))
        strat    = float(STRATEGIC_MAPPING.get(self.var_strategic.get(), 0.0))
        area     = float(self.var_area.get())
        units    = distinct * cond * strat * area * spat * diff * year_mult
        self.gain_result.config(text=f"Biodiversity Units: {units:.3f}")

    def _add_gain_to_list(self):
        self._calculate_gain()
        result_text = self.gain_result.cget("text")
        if "Biodiversity Units:" not in result_text:
            messagebox.showwarning("No calculation", "Please calculate first"); return
        units = float(result_text.split(":")[1].strip())
        broad = self.var_broad.get().strip()
        specific = self.var_specific.get()
        item = {
            "plot_code":          self.var_code.get().strip(),
            "habitat":            f"{broad} - {specific}" if broad and specific else "Unknown Habitat",
            "area":               float(self.var_area.get()) if self.var_area.get() else 0,
            "units":              units,
            "broad":              broad,
            "specific":           specific,
            "condition":          self.var_condition.get(),
            "difficulty":         self.var_difficulty.get(),
            "spatial":            self.var_spatial.get(),
            "strategic":          self.var_strategic.get(),
            "years":              self.var_year.get(),
            "distinctiveness":    self.lbl_distinct.cget("text").split(":")[1].strip(),
            "condition_score":    self.lbl_cond.cget("text").split(":")[1].strip(),
            "difficulty_score":   self.lbl_diff.cget("text").split(":")[1].strip(),
            "spatial_multiplier": self.lbl_spat.cget("text").split(":")[1].strip(),
            "strategic_multiplier": self.lbl_strat.cget("text").split(":")[1].strip(),
            "year_multiplier":    self.lbl_yearmult.cget("text").split(":")[1].strip(),
        }
        self.gain_items.append(item)
        self.gain_total += units
        self._refresh_gain_tree()
        messagebox.showinfo("Added",
            f"Added: {item['habitat']}\nBiodiversity Units: {units:.3f}\nNew Total: {self.gain_total:.3f}")

    def _refresh_gain_tree(self):
        for item in self.gain_tree.get_children():
            self.gain_tree.delete(item)
        for i, item in enumerate(self.gain_items):
            self.gain_tree.insert("", "end", iid=str(i), values=(
                item.get("plot_code", ""), item["habitat"],
                f"{item['area']:.2f}", f"{item['units']:.3f}", "Delete ❌"))
        if self.gain_total_label:
            self.gain_total_label.config(text=f"Total Biodiversity Units: {self.gain_total:.3f}")

    def _remove_gain_item(self, event):
        selection = self.gain_tree.selection()
        if not selection:
            return
        item_iid   = selection[0]
        item_values = self.gain_tree.item(item_iid, "values")
        removed_habitat = item_values[1] if len(item_values) > 1 else "Unknown"
        try:
            removed_units = float(item_values[3]) if len(item_values) > 3 else 0
        except (ValueError, IndexError):
            removed_units = 0
        item = self.gain_tree.item(item_iid, "values")
# Match by content rather than index:
        self.gain_items = [g for g in self.gain_items 
                   if not (g.get("plot_code","") == item[0] and 
                           f"{g['area']:.2f}" == item[2])]
        self.gain_total = sum(i["units"] for i in self.gain_items)
        self._refresh_gain_tree()
        
        messagebox.showinfo("Removed",
            f"Removed: {removed_habitat}\nUnits removed: {removed_units:.3f}\n"
            f"Plot code: {item_values[0] if item_values else 'N/A'}")

    def _clear_gain_list(self):
        if self.gain_items and messagebox.askyesno("Clear All", "Clear all saved calculations?"):
            self.gain_items = []
            self.gain_total = 0.0
            self._refresh_gain_tree()

    def _save_gain_selection(self):
        if not self.gain_items:
            messagebox.showwarning("No data", "Add calculations using 'Add to List' first."); return
        choice = messagebox.askyesno("Save Options",
            "Save ALL calculations in the list?\n\nYes = Save all\nNo = Save only current calculation")
        if choice:
            self._save_all_gain_items()
        else:
            self._save_single_gain_item()

    def _save_single_gain_item(self):
        self._calculate_gain()
        txt = self.gain_result.cget("text")
        if "Biodiversity Units:" not in txt:
            messagebox.showerror("No result", "Calculate before saving."); return
        row = {
            "Timestamp":            time.strftime("%Y-%m-%d %H:%M:%S"),
            "Plot code":            self.var_code.get(),
            "Broad Habitat":        self.var_broad.get(),
            "Specific Habitat":     self.var_specific.get(),
            "Distinctiveness":      self.lbl_distinct.cget("text").split(":")[1].strip(),
            "Years":                self.var_year.get(),
            "Year Multiplier":      self.lbl_yearmult.cget("text").split(":")[1].strip(),
            "Condition":            self.var_condition.get(),
            "Condition Score":      self.lbl_cond.cget("text").split(":")[1].strip(),
            "Difficulty":           self.var_difficulty.get(),
            "Difficulty Score":     self.lbl_diff.cget("text").split(":")[1].strip(),
            "Spatial Risk":         self.var_spatial.get(),
            "Spatial Multiplier":   self.lbl_spat.cget("text").split(":")[1].strip(),
            "Strategic Significance": self.var_strategic.get(),
            "Strategic multiplier": self.lbl_strat.cget("text").split(":")[1].strip(),
            "Area (ha)":            self.var_area.get(),
            "Biodiversity Units":   txt.split(":")[1].strip(),
        }
        default_name = f"gain_selection_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        savepath = filedialog.asksaveasfilename(
            initialfile=default_name, defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        target = savepath if savepath else str(BASE_DIR / default_name)
        try:
            with open(target, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                writer.writeheader(); writer.writerow(row)
            messagebox.showinfo("Saved", f"Saved to: {target}")
        except Exception as e:
            messagebox.showerror("Save error", str(e)); return
        self.saved_rows.append(row)
        self._refresh_saved_table()

    def _save_all_gain_items(self):
        if not self.gain_items:
            return
        savepath = filedialog.asksaveasfilename(defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"gain_calculations_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        if not savepath:
            return
        fieldnames = ["Timestamp", "Plot code", "Broad Habitat", "Specific Habitat", "Distinctiveness",
                      "Years", "Year Multiplier", "Condition", "Condition Score",
                      "Difficulty", "Difficulty Score", "Spatial Risk", "Spatial Multiplier",
                      "Strategic Significance", "Strategic multiplier", "Area (ha)", "Biodiversity Units"]
        try:
            with open(savepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for item in self.gain_items:
                    row = {
                        "Timestamp":              time.strftime("%Y-%m-%d %H:%M:%S"),
                        "Plot code":              item.get("plot_code", ""),
                        "Broad Habitat":          item.get("broad", ""),
                        "Specific Habitat":       item.get("specific", ""),
                        "Distinctiveness":        item.get("distinctiveness", ""),
                        "Years":                  item.get("years", ""),
                        "Year Multiplier":        item.get("year_multiplier", ""),
                        "Condition":              item.get("condition", ""),
                        "Condition Score":        item.get("condition_score", ""),
                        "Difficulty":             item.get("difficulty", ""),
                        "Difficulty Score":       item.get("difficulty_score", ""),
                        "Spatial Risk":           item.get("spatial", ""),
                        "Spatial Multiplier":     item.get("spatial_multiplier", ""),
                        "Strategic Significance": item.get("strategic", ""),
                        "Strategic multiplier":   item.get("strategic_multiplier", ""),
                        "Area (ha)":              item.get("area", ""),
                        "Biodiversity Units":     item.get("units", 0),
                    }
                    writer.writerow(row)
                    self.saved_rows.append(row)
            messagebox.showinfo("Saved", f"All {len(self.gain_items)} calculations saved to:\n{savepath}")
            self._refresh_saved_table()
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    # -------------------- Saved Results Tab --------------------
    def _build_saved_tab(self):
        card = ttk.Frame(self.tab_saved, padding=12)
        card.pack(fill="both", expand=True, padx=10, pady=8)
        cols = ["Timestamp", "Plot code", "Broad Habitat", "Specific Habitat", "Distinctiveness",
                "Years", "Condition", "Spatial Risk", "Strategic Significance", "Difficulty",
                "Area (ha)", "Biodiversity Units"]
        self.saved_tree = ttk.Treeview(card, columns=cols, show="headings", height=14)
        for c in cols:
            self.saved_tree.heading(c, text=c)
            self.saved_tree.column(c, width=100, anchor="w")
        self.saved_tree.pack(fill="both", expand=True)
        btns = ttk.Frame(card)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="Export All to CSV", command=self._export_saved_all).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear Saved Rows",  command=self._clear_saved).pack(side="left", padx=6)

    def _refresh_saved_table(self):
        for i in self.saved_tree.get_children():
            self.saved_tree.delete(i)
        for row in self.saved_rows:
            self.saved_tree.insert("", "end", values=(
                row.get("Timestamp"), row.get("Plot code"), row.get("Broad Habitat"),
                row.get("Specific Habitat"), row.get("Distinctiveness"), row.get("Years"),
                row.get("Condition"), row.get("Spatial Risk"), row.get("Strategic Significance"),
                row.get("Difficulty"), row.get("Area (ha)"), row.get("Biodiversity Units")))

    def _export_saved_all(self):
        if not self.saved_rows:
            messagebox.showinfo("No data", "No saved rows to export."); return
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not p:
            return
        try:
            with open(p, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(self.saved_rows[0].keys()))
                writer.writeheader()
                for r in self.saved_rows:
                    writer.writerow(r)
            messagebox.showinfo("Exported", f"Saved results exported to {p}")
        except Exception as e:
            messagebox.showerror("Export error", str(e))

    def _clear_saved(self):
        if messagebox.askyesno("Confirm", "Clear all saved rows?"):
            self.saved_rows = []
            self._refresh_saved_table()


# -------------------- Run --------------------
def main():
    root = tk.Tk()
    root.configure(bg=MAIN_BG)
    try:
        ico_path = LOGOS_DIR / "biodiversity.ico"
        if ico_path.exists():
            root.iconbitmap(str(ico_path))
        else:
            alt = BASE_DIR / "biodiversity.ico"
            if alt.exists():
                root.iconbitmap(str(alt))
    except Exception:
        pass
    app = BiodiversityApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
