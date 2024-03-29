from fastapi import FastAPI

import psycopg2
from pydantic import BaseModel
import os
import uuid
import psycopg2.extras

# fixes psycopg2.ProgrammingError: can't adapt type 'UUID'
psycopg2.extras.register_uuid()

POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]

con = psycopg2.connect(
    dbname="postgres",
    user="postgres",
    password=POSTGRES_PASSWORD,
    host="database",
    port="5432",
)


def get_ancestors(device_id):
    with con:
        with con.cursor() as cur:
            cur.execute(
                """WITH RECURSIVE ancestor(n)
                AS (
                    VALUES(%s)
                    UNION
                    SELECT node_from FROM predicate_graph_edge, ancestor
                    WHERE predicate_graph_edge.node_to=ancestor.n
                )
                SELECT k_number, date_received, generic_name, device_name, product_code, node_to, node_from
                FROM predicate_graph_edge
                JOIN device ON node_from = device.k_number
                WHERE predicate_graph_edge.node_to IN (SELECT n FROM ancestor);""",
                [device_id],
            )

            return cur.fetchall()


def get_device_recalls(device_id):
    with con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT device_recall.k_number, recall_id, recall.reason_for_recall
                FROM device_recall
                LEFT JOIN recall ON device_recall.recall_id = recall.id
                WHERE device_recall.k_number = %s;
                """,
                [device_id],
            )
            return cur.fetchall()


# "Because the UNION ALL operator does not remove duplicate rows, it runs faster than the UNION operator."
# DOES NOT APPLY HERE lol
# query went from ~18s to 0.015s by removing "ALL"
def get_ancestry_recalls(device_id):
    with con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT device.k_number, recall_id, recall.reason_for_recall
                        FROM (
                            WITH RECURSIVE ancestor(n)
                            AS (
                                VALUES(%s)
                                UNION
                                SELECT node_from FROM predicate_graph_edge, ancestor
                                WHERE predicate_graph_edge.node_to=ancestor.n
                            )
                            SELECT node_to, node_from
                            FROM predicate_graph_edge
                            WHERE predicate_graph_edge.node_to IN (SELECT n FROM ancestor)
                        ) ancestry
                        JOIN device ON ancestry.node_from = device.k_number
                        LEFT JOIN device_recall ON device_recall.k_number = device.k_number
                        LEFT JOIN recall ON device_recall.recall_id = recall.id;
                """,
                [device_id],
            )
            return cur.fetchall()


def get_device(device_id):
    with con:
        with con.cursor() as cur:
            cur.execute(
                """SELECT k_number, date_received, device_name, product_code FROM device WHERE k_number = %s""",
                [device_id],
            )

            return cur.fetchall()[0]


def format_row(row):
    return {
        "k_number": row[0],
        "date_received": row[1],
        "generic_name": row[2],
        "device_name": row[3],
        "product_code": row[4],
        "node_to": row[5],
        "node_from": row[6],
    }


def format_node(row):
    return {
        "id": row["k_number"],
        "date": row["date_received"],
        "product_code": row["product_code"],
        "name": row["device_name"],
    }


def format_edge(row):
    return {"source": row["node_from"], "target": row["node_to"]}


async def get_ancestry_graph(device_id):
    ancestors = get_ancestors(device_id)

    # fetch the recalls separately because `GROUP BY k_number` kills the query time
    # compared to doing it in memory

    recalls_map = {}
    device_recalls = get_device_recalls(device_id)
    for recall in device_recalls:
        k_number = recall[0]
        recall_id = recall[1]
        recall_reason = recall[2]

        if k_number not in recalls_map:
            recalls_map[k_number] = []

        if recall_id is not None:
            recalls_map[k_number].append(
                {"recall_id": recall_id, "reason": recall_reason}
            )

    ancestry_recalls = get_ancestry_recalls(device_id)
    for recall in ancestry_recalls:

        # device.k_number, recall_id, recall.reason_for_recall
        k_number = recall[0]
        recall_id = recall[1]
        recall_reason = recall[2]

        if k_number not in recalls_map:
            recalls_map[k_number] = []

        if recall_id is not None:
            recalls_map[k_number].append(
                {"recall_id": recall_id, "reason": recall_reason}
            )

    device = get_device(device_id)
    res = list(map(format_row, ancestors))

    edges = list(map(format_edge, res))
    nodes = list(map(format_node, res))

    nodes.append(
        {
            # k_number, date_received, device_name, product_code
            "id": device[0],
            "date": device[1],
            "name": device[2],
            "product_code": device[3],
        }
    )

    for node in nodes:
        node["recalls"] = recalls_map.get(node["id"], [])

    unique_nodes = []
    seen_nodes = set()

    names = {}

    # assert names are the same for the same ID
    for node in res:
        if node["k_number"] in names:
            assert node["device_name"] == names[node["k_number"]]
        names[node["k_number"]] = node["device_name"]

        assert node["k_number"] == node["node_from"]

    for node in nodes:
        if not node["id"] in seen_nodes:
            unique_nodes.append(node)

        seen_nodes.add(node["id"])

    product_descriptions = {}

    for row in res:
        product_descriptions[row["product_code"]] = row["generic_name"]

    return {
        "nodes": unique_nodes,
        "links": edges,
        "product_descriptions": product_descriptions,
    }


def format_row_search(row):
    return {
        "k_number": row[0],
        "date_received": row[1],
        "device_name": row[2],
        "product_code": row[3],
    }


async def device_search(query, offset, limit):
    rows = []
    wildcard_query = "%" + query + "%"
    limit = min(50, limit)
    with con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT k_number, date_received, device_name, product_code "
                "FROM device WHERE (k_number ILIKE %s OR (device_name ILIKE %s) OR similarity(device_name, %s) > 0.3) "
                "ORDER BY (k_number ILIKE %s) DESC, "
                "(device_name ILIKE %s) DESC, "
                "similarity(device_name, %s) DESC, "
                "date_received DESC "
                "LIMIT %s OFFSET %s",
                [
                    wildcard_query,
                    wildcard_query,
                    wildcard_query,
                    wildcard_query,
                    wildcard_query,
                    wildcard_query,
                    limit,
                    offset,
                ],
            )
            rows = cur.fetchall()

            cur.execute(
                "SELECT COUNT(*) "
                "FROM device WHERE (k_number ILIKE %s OR (device_name ILIKE %s) OR  similarity(device_name, %s) > 0.3);",
                [wildcard_query, wildcard_query, wildcard_query],
            )

            count = cur.fetchall()[0][0]

    return {
        "data": list(map(format_node, map(format_row_search, rows))),
        "total_count": count,
    }


def store_feedback(feedback):
    with con:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO feedback VALUES (%s, %s, %s, %s)",
                [
                    uuid.uuid4(),
                    feedback.name,
                    feedback.email,
                    feedback.message,
                ],
            )


# DB model
class Test(BaseModel):
    field_1: str


app = FastAPI()


@app.get("/ancestry/{device_number}")  # , response_model=list[Test]
async def read_user_details(device_number):
    return await get_ancestry_graph(device_number)


@app.get("/search")
async def put_user_details(query, offset: int = 0, limit: int = 10):
    return await device_search(query, offset, limit)


class Feedback(BaseModel):
    name: str | None = None
    email: str | None = None
    message: str


@app.post("/submit_feedback")
async def submit_feedback(feedback: Feedback):
    store_feedback(feedback)
    return {"message": "success"}
