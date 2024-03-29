import ujson as json
import sqlite3
import uuid

con = sqlite3.connect("devices.db")

cur = con.cursor()
cur.execute(
    "CREATE TABLE IF NOT EXISTS device("
    "k_number TEXT PRIMARY KEY,"
    "date_received TEXT,"
    "generic_name TEXT,"
    "device_name TEXT,"
    "product_code TEXT,"
    "statement_or_summary TEXT);"
)

cur.execute(
    "CREATE TABLE IF NOT EXISTS recall("
    "id TEXT PRIMARY KEY,"
    "product_code TEXT,"
    "event_date_initiated TEXT,"
    "recall_status TEXT,"
    "reason_for_recall TEXT);"
)

cur.execute(
    "CREATE TABLE IF NOT EXISTS device_recall("
    "recall_id TEXT,"
    "k_number TEXT,"
    "FOREIGN KEY(recall_id) REFERENCES recall(id),"
    "FOREIGN KEY(k_number) REFERENCES device(k_number));"
)

cur.execute(
    "CREATE TABLE IF NOT EXISTS predicate_graph_edge("
    "node_from TEXT,"
    "node_to TEXT,"
    "FOREIGN KEY(node_from) REFERENCES device(k_number),"
    "FOREIGN KEY(node_to) REFERENCES device(k_number),"
    "PRIMARY KEY(node_from, node_to));"
)

from progress.bar import Bar


print("Reading 510(k) JSON file")
with open("device-510k-0001-of-0001.json") as f:
    data = json.load(f)

    bar = Bar("Importing data to sqlite", max=len(data["results"]))

    for device in data["results"]:
        bar.next()

        # skip the DE NOVO devices
        if "DEN" not in device.get("k_number"):
            vals = (
                device.get("k_number"),
                device.get("date_received"),
                device["openfda"].get("device_name"),
                device.get("device_name"),
                device.get("product_code"),
                device.get("statement_or_summary"),
            )

            try:
                cur.execute("INSERT INTO device VALUES(?, ?, ?, ?, ?, ?)", vals)
            except Exception:
                continue

    con.commit()

print()

print("Reading recalls JSON file")
with open("device-recall-0001-of-0001.json") as f:
    data = json.load(f)

    bar = Bar("Importing data to sqlite", max=len(data["results"]))

    for recall in data["results"]:
        bar.next()

        id = recall.get("cfres_id")
        if not id:
            id = str(uuid.uuid4())

        vals = (
            id,
            recall.get("product_code"),
            recall.get("event_date_initiated"),
            recall.get("recall_status"),
            recall.get("reason_for_recall"),
        )

        try:
            cur.execute("INSERT INTO recall VALUES(?, ?, ?, ?, ?)", vals)

            for k_number in recall.get("k_numbers", []):
                cur.execute("INSERT INTO device_recall VALUES(?, ?)", (id, k_number))
        except Exception:
            continue

    con.commit()

print()
