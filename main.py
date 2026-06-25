import csv
import io
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

import requests

ZIP_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ENHANCED.ZIP"
NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ENHANCED_XML"
NSMAP = {"s": NS}
DCA_PREFIX = "Digital Currency Address - "
ENTITY_TAG = f"{{{NS}}}entity"
FEATURE_TAG = f"{{{NS}}}feature"


def download_xml(save_path="sdn_enhanced.xml"):
    print(f"downloading {ZIP_URL}", file=sys.stderr)
    resp = requests.get(ZIP_URL, timeout=120)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        inner = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        with zf.open(inner) as src, open(save_path, "wb") as dst:
            dst.write(src.read())
    return save_path


def primary_name(entity):
    for name in entity.findall("s:names/s:name", NSMAP):
        if name.find("s:aliasType", NSMAP) is None:
            return name.findtext("s:translations/s:translation/s:formattedFullName", "", NSMAP)
    return ""


def extract_addresses(xml_path):
    results, seen = [], set()
    for _, entity in ET.iterparse(xml_path):
        if entity.tag != ENTITY_TAG:
            continue
        name = primary_name(entity)
        for feature in entity.iter(FEATURE_TAG):
            kind = feature.findtext("s:type", "", NSMAP)
            value = feature.findtext("s:value", "", NSMAP)
            if kind.startswith(DCA_PREFIX) and value.startswith("0x"):
                key = (value.lower(), name)
                if key not in seen:
                    seen.add(key)
                    results.append((value, name))
        entity.clear()
    return results


def load_existing(path="data.csv"):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {(r["address"].lower(), r["name"]) for r in csv.DictReader(f)}


def write_data(results, path="data.csv"):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("address,name\n")
        for address, name in results:
            escaped = name.replace('"', '""')
            f.write(f'{address},"{escaped}"\n')


def write_readme_stats(results, path="README.md"):
    counts = {}
    for _, name in results:
        counts[name] = counts.get(name, 0) + 1
    rows = "\n".join(f"| {name} | {n} |" for name, n in sorted(counts.items()))
    table = f"| sanctioned entity | count |\n| :- | -: |\n{rows}\n| **total** | **{len(results)}** |"

    with open(path, encoding="utf-8") as f:
        head = f.read().split("## stats")[0].rstrip()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{head}\n\n## stats\n\n{table}\n")


def main():
    xml_path = download_xml()
    existing = load_existing()
    results = extract_addresses(xml_path)

    write_data(results)
    write_readme_stats(results)

    current = {(a.lower(), n) for a, n in results}
    added = [(a, n) for a, n in results if (a.lower(), n) not in existing]
    removed = sorted(existing - current)

    if added or removed:
        print(f"changes detected: {len(added)} added, {len(removed)} removed")
        for address, name in added:
            print(f"  + {address} - {name}")
        for address, name in removed:
            print(f"  - {address} - {name}")
    else:
        print("no changes - data.csv already up to date")
    print(f"wrote {len(results)} addresses to data.csv")


if __name__ == "__main__":
    main()
