from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import geopandas as gpd
import tempfile
import zipfile
import shutil
import json
from shapely import wkt

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEOJSON_PATH = "backend/data/layer.geojson"


@app.post("/upload/")
async def upload_shapefile(file: UploadFile = File(...)):
    tmp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(tmp_dir, file.filename)

    with open(zip_path, "wb") as f:
        f.write(await file.read())

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(tmp_dir)

    shp_files = [f for f in os.listdir(tmp_dir) if f.endswith('.shp')]
    if not shp_files:
        shutil.rmtree(tmp_dir)
        return {"error": "SHP файл не найден в архиве"}

    shp_path = os.path.join(tmp_dir, shp_files[0])
    cpg_path = shp_path.replace('.shp', '.cpg')
    encoding = None

    if os.path.exists(cpg_path):
        with open(cpg_path, 'r') as cpg_file:
            encoding = cpg_file.read().strip()
    else:
        with open(cpg_path, 'w', encoding='utf-8') as cpg_file:
            cpg_file.write("CP1251")
        encoding = "cp1251"

    try:
        gdf = gpd.read_file(shp_path, encoding=encoding)
    except Exception:
        gdf = gpd.read_file(shp_path, encoding="cp1251")

    if gdf.geometry.isnull().all() and 'wkt_geom' in gdf.columns:
        gdf['geometry'] = gdf['wkt_geom'].apply(wkt.loads)
        gdf = gdf.set_geometry('geometry')

    print("🔍 Загруженные столбцы:", gdf.columns.tolist())

    # Убедимся, что нужные поля есть
    for col in ["NameRegion", "ur_pop25", "pop25"]:
        if col not in gdf.columns:
            gdf[col] = 0.0

    # Универсальный парсер чисел
    def parse_float(val):
        try:
            return float(str(val).strip().replace(",", ".").replace(" ", ""))
        except:
            return 0.0

    gdf["ur_pop25"] = gdf["ur_pop25"].apply(parse_float)
    gdf["pop25"] = gdf["pop25"].apply(parse_float)

    # Добавление данных по естественному приросту (2021–2024)
    for year in range(21, 24):
        col = f"nat_inc{year}"
        if col not in gdf.columns:
            gdf[col] = 0.0
        else:
            gdf[col] = gdf[col].apply(parse_float)

    # Расчёт уровня урбанизации
    gdf["urbanization_rate"] = gdf.apply(
        lambda row: round((row["ur_pop25"] / row["pop25"]) * 100, 2) if row["pop25"] > 0 else 0.0,
        axis=1
    )

    gdf = gdf.rename(columns={"NameRegion": "region"})

    os.makedirs(os.path.dirname(GEOJSON_PATH), exist_ok=True)
    gdf.to_file(GEOJSON_PATH, driver="GeoJSON", encoding="utf-8")
    shutil.rmtree(tmp_dir)
    return {"status": "uploaded", "features": len(gdf)}


@app.get("/geojson")
def get_geojson():
    try:
        with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse(content=data, media_type="application/json; charset=utf-8")
    except FileNotFoundError:
        return JSONResponse(content={"error": "GeoJSON файл не найден"}, status_code=404)
