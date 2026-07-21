"""
DESCUBRIDOR DEL ENDPOINT DE ALERTAS DE SENAPRED
================================================

SENAPRED no publica una API documentada, pero mantiene un dashboard
oficial de ArcGIS ("ALERTAS SENAPRED") respaldado por un FeatureServer
público y consultable. Este script parte de ese dashboard, encuentra la
capa de datos que lo alimenta, y te imprime:

  1. La URL del FeatureServer/capa (lo que pondrás en fuentes.py).
  2. Los nombres REALES de los campos (para mapear tipo/color/comuna/etc).
  3. Una muestra de alertas actuales.

Ejecuta esto UNA VEZ en una máquina con internet:

    pip install requests
    python descubrir_senapred.py

Luego copia la URL que imprime en SENAPRED_ARCGIS_URL dentro de fuentes.py.

Nota: el ID del dashboard puede cambiar si SENAPRED lo republica. Si el
script no encuentra nada, abre https://www.arcgis.com/apps/dashboards/bdc345e01e324af490800634d0f0e3a5
en el navegador, verifica que exista, o busca "ALERTAS SENAPRED" en
arcgis.com y actualiza DASHBOARD_ITEM_ID abajo.
"""

from __future__ import annotations
import json
import re
import requests

DASHBOARD_ITEM_ID = "bdc345e01e324af490800634d0f0e3a5"  # "ALERTAS SENAPRED"
ARCGIS = "https://www.arcgis.com/sharing/rest/content/items"


def _get(url, **params):
    params.setdefault("f", "json")
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def encontrar_urls_featureserver() -> list[str]:
    """Lee la config del dashboard y extrae las URLs/itemIds de sus capas."""
    urls: set[str] = set()
    item_ids: set[str] = set()

    # 1. La configuración del dashboard (dónde viven las referencias a datos).
    data = _get(f"{ARCGIS}/{DASHBOARD_ITEM_ID}/data")
    blob = json.dumps(data)

    # URLs de FeatureServer/MapServer embebidas directamente en la config.
    for m in re.findall(r'https?://[^"]+?/(?:Feature|Map)Server(?:/\d+)?', blob):
        urls.add(m)

    # itemIds de capas referenciadas por ID (hay que resolverlos a su URL).
    for m in re.findall(r'"itemId"\s*:\s*"([0-9a-f]{32})"', blob):
        item_ids.add(m)
    for m in re.findall(r'"datasets?".*?"itemId"\s*:\s*"([0-9a-f]{32})"', blob):
        item_ids.add(m)

    # 2. Resuelve cada itemId de capa a su URL de servicio.
    for iid in item_ids:
        try:
            meta = _get(f"{ARCGIS}/{iid}")
            if meta.get("url") and "Server" in meta["url"]:
                urls.add(meta["url"])
        except Exception:
            pass

    return sorted(urls)


def inspeccionar_capa(url: str) -> None:
    """Consulta una capa y muestra sus campos y una muestra de registros."""
    # Asegura que apuntamos a una capa concreta (…/FeatureServer/0).
    if re.search(r'/(Feature|Map)Server$', url):
        url = url + "/0"

    print(f"\n{'='*70}\nCAPA: {url}\n{'='*70}")

    # Metadatos de la capa: nombre y campos.
    try:
        meta = _get(url)
        print(f"Nombre: {meta.get('name')}")
        campos = meta.get("fields", [])
        if campos:
            print("\nCAMPOS DISPONIBLES (usa estos nombres en fuentes.py):")
            for c in campos:
                print(f"  - {c['name']:<28} ({c.get('type','?').replace('esriFieldType','')}) "
                      f"{c.get('alias','')}")
    except Exception as e:
        print(f"  No se pudieron leer metadatos: {e}")

    # Muestra de registros activos.
    try:
        q = _get(f"{url}/query", where="1=1", outFields="*",
                 returnGeometry="false", resultRecordCount=3)
        feats = q.get("features", [])
        print(f"\nMUESTRA ({len(feats)} registros):")
        for f in feats:
            print("  " + json.dumps(f.get("attributes", {}), ensure_ascii=False)[:300])
    except Exception as e:
        print(f"  No se pudo consultar: {e}")


def main():
    print("Buscando el FeatureServer del dashboard oficial de SENAPRED...\n")
    try:
        urls = encontrar_urls_featureserver()
    except Exception as e:
        print(f"Error accediendo al dashboard: {e}")
        print("Verifica el DASHBOARD_ITEM_ID o tu conexión.")
        return

    if not urls:
        print("No se encontraron capas en la config del dashboard.")
        print("Es posible que SENAPRED haya cambiado el ID. Revisa el dashboard "
              "en el navegador y actualiza DASHBOARD_ITEM_ID.")
        return

    print(f"Se encontraron {len(urls)} capa(s):")
    for u in urls:
        print(f"  · {u}")

    for u in urls:
        inspeccionar_capa(u)

    print(f"\n{'='*70}")
    print("SIGUIENTE PASO: copia la URL de la capa de ALERTAS de arriba en")
    print("SENAPRED_ARCGIS_URL dentro de fuentes.py, y ajusta los nombres de")
    print("campo (tipo/color/comuna/región/estado) según lo que veas aquí.")
    print('='*70)


if __name__ == "__main__":
    main()
