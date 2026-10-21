"""Neo4j GraphRAG integration over the museum knowledge graph."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from neo4j import GraphDatabase
from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.indexes import create_vector_index
from neo4j_graphrag.retrievers import HybridCypherRetriever
from neo4j_graphrag.types import RetrieverResultItem

KB_VERSION = "gim-graph-v2"
URI = "bolt://127.0.0.1:17687"
PASSWORD = "gim-local-graph-2026"
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GRAPH_QUERY = """
WITH node, score
WHERE node.roles IS NULL OR $role IN node.roles
OPTIONAL MATCH (node)-[edge]-(related:Fact)
WHERE related.roles IS NULL OR $role IN related.roles
WITH node, score, collect(DISTINCT {type: type(edge), id: related.id, title: related.title}) AS links
RETURN node.title + ': ' + node.text + ' | Связи: ' +
       reduce(s = '', link IN links | s + coalesce(link.type, '') + ': ' + coalesce(link.title, '') + '; ') AS content,
       score AS score,
       {doc_id: node.id, version_id: node.version_id, source_path: node.source_path,
        quote: node.quote, links: links} AS metadata
"""

HISTORICAL_QUERY = """
WITH node, score
WHERE size(node.role_ids) = 0 OR $role IN node.role_ids
OPTIONAL MATCH (node)-[:INTERESTED_IN]->(related:Role)
WITH node, score, collect(DISTINCT related.title) AS roles
RETURN 'Историческая обезличенная анкета. Направления: ' +
       reduce(s = '', title IN roles | s + coalesce(title, '') + '; ') +
       ' Ответы: ' + node.text AS content,
       score AS score,
       {doc_id: node.id, version_id: node.version_id,
        source_path: node.source_path, quote: '', links: roles} AS metadata
"""


def _driver():
    return GraphDatabase.driver(
        os.getenv("GIM_NEO4J_URI", URI),
        auth=(os.getenv("GIM_NEO4J_USER", "neo4j"), os.getenv("GIM_NEO4J_PASSWORD", PASSWORD)),
    )


def _format(record):
    return RetrieverResultItem(content=record["content"], metadata=record["metadata"])


def embed_facts() -> int:
    """Embed approved facts and training-only historical applications."""
    key = os.getenv("OPENAI_API_KEY", "")
    if not key or key.startswith("replace-"):
        raise RuntimeError("OPENAI_API_KEY is required to embed graph facts")
    embedder = OpenAIEmbeddings(model="text-embedding-3-small", api_key=key)
    with _driver() as driver:
        total = 0
        for label, index in (("Fact", "gim_fact_vector"),
                             ("HistoricalApplication", "gim_historical_vector")):
            create_vector_index(driver, index, label, "embedding", 1536, "cosine")
            records, _, _ = driver.execute_query(
                f"MATCH (n:{label}) WHERE n.embedding IS NULL RETURN n.id AS id, n.title AS title, n.text AS text",
                database_="neo4j",
            )
            for record in records:
                vector = embedder.embed_query((record["title"] or "Анкета ГИМ") + "\n" + (record["text"] or ""))
                driver.execute_query(
                    f"MATCH (n:{label} {{id: $id}}) SET n.embedding = $vector",
                    {"id": record["id"], "vector": vector}, database_="neo4j",
                )
            total += len(records)
    return total


def retrieve(role: str, question: str, *, top_k: int = 8) -> list[dict]:
    """Official Neo4j hybrid retriever expands matching facts along graph edges."""
    key = os.getenv("OPENAI_API_KEY", "")
    if not key or key.startswith("replace-"):
        raise RuntimeError("OPENAI_API_KEY is required for hybrid GraphRAG retrieval")
    with _driver() as driver:
        create_vector_index(driver, "gim_fact_vector", "Fact", "embedding", 1536, "cosine")
        create_vector_index(driver, "gim_historical_vector", "HistoricalApplication", "embedding", 1536, "cosine")
        embedder = OpenAIEmbeddings(model="text-embedding-3-small", api_key=key)
        retriever = HybridCypherRetriever(
            driver=driver,
            vector_index_name="gim_fact_vector",
            fulltext_index_name="gim_fact_text",
            retrieval_query=GRAPH_QUERY,
            embedder=embedder,
            result_formatter=_format,
        )
        result = retriever.search(query_text=question, top_k=top_k,
                                  effective_search_ratio=4, query_params={"role": role})
        historical_retriever = HybridCypherRetriever(
            driver=driver, vector_index_name="gim_historical_vector",
            fulltext_index_name="gim_historical_text", retrieval_query=HISTORICAL_QUERY,
            embedder=embedder, result_formatter=_format,
        )
        examples = historical_retriever.search(query_text=question, top_k=2,
                                               effective_search_ratio=8,
                                               query_params={"role": role})
        role_records, _, _ = driver.execute_query(
            """MATCH (role:Role {id: $role_id})
            OPTIONAL MATCH (role)-[edge]-(fact:Fact)
            WHERE fact.roles IS NULL OR $role IN fact.roles
            WITH role, collect(DISTINCT {id: fact.id, title: fact.title,
                 text: fact.text, source_path: fact.source_path, quote: fact.quote,
                 version_id: fact.version_id, relation: type(edge)}) AS neighbors
            RETURN role.id AS id, role.title AS title, role.text AS text,
                   role.source_path AS source_path, role.quote AS quote,
                   role.version_id AS version_id, neighbors""",
            {"role_id": "role:" + role, "role": role}, database_="neo4j",
        )
    chunks = [{"doc_id": item.metadata["doc_id"],
             "version_id": item.metadata["version_id"],
             "source_path": item.metadata["source_path"],
             "quote": item.metadata["quote"],
             "text": item.content,
             "links": item.metadata["links"]} for item in result.items]
    chunks.extend({"doc_id": item.metadata["doc_id"],
                   "version_id": item.metadata["version_id"],
                   "source_path": item.metadata["source_path"],
                   "quote": item.metadata["quote"],
                   "text": item.content,
                   "links": item.metadata["links"],
                   "kind": "historical_training_example"} for item in examples.items)
    if role_records:
        role_fact = role_records[0]
        chunks.insert(0, {"doc_id": role_fact["id"], "version_id": role_fact["version_id"],
                          "source_path": role_fact["source_path"], "quote": role_fact["quote"],
                          "text": role_fact["title"] + ": " + role_fact["text"],
                          "links": role_fact["neighbors"]})
    return list({chunk["doc_id"]: chunk for chunk in chunks}.values())


def facts_for_review(role: str, query: str = "музей", *, top_k: int = 8) -> list[dict]:
    """Read the local graph through Neo4j full-text search without an API call."""
    with _driver() as driver:
        records, _, _ = driver.execute_query(
            """CALL db.index.fulltext.queryNodes('gim_fact_text', $query) YIELD node, score
            WHERE node.roles IS NULL OR $role IN node.roles
            OPTIONAL MATCH (node)-[edge]-(related:Fact)
            WHERE related.roles IS NULL OR $role IN related.roles
            WITH node, score, collect(DISTINCT {type: type(edge), id: related.id}) AS links
            RETURN node.id AS doc_id, node.title AS title, node.text AS text,
                   node.source_path AS source_path, node.quote AS quote,
                   node.version_id AS version_id, links
            ORDER BY score DESC LIMIT $top_k""",
            {"query": query, "role": role, "top_k": top_k},
            database_="neo4j",
        )
    return [dict(record) for record in records]
