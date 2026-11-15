"""Adaptador de Cosmos DB (NoSQL) con DefaultAzureCredential. Sin llaves."""
from __future__ import annotations

import os
from typing import Callable

from azure.core import MatchConditions
from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential

REINTENTOS_ETAG = 5


class RepositorioCosmos:
    def __init__(self, client: CosmosClient | None = None):
        if client is None:
            client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=DefaultAzureCredential())
        base = client.get_database_client(os.environ.get("COSMOS_BASE", "telecorp"))
        self._incidencias = base.get_container_client(
            os.environ.get("COSMOS_CONTENEDOR_INCIDENCIAS", "incidencias"))
        self._zonas = base.get_container_client(
            os.environ.get("COSMOS_CONTENEDOR_ZONAS", "zonas"))
        self._eventos = base.get_container_client(
            os.environ.get("COSMOS_CONTENEDOR_EVENTOS", "eventos_procesados"))

    # -- idempotencia
    def reclamar_evento(self, ce_id: str) -> bool:
        try:
            self._eventos.create_item({"id": ce_id})
            return True
        except exceptions.CosmosResourceExistsError:
            return False

    def liberar_evento(self, ce_id: str) -> None:
        try:
            self._eventos.delete_item(ce_id, partition_key=ce_id)
        except exceptions.CosmosResourceNotFoundError:
            pass

    # -- incidencias
    def obtener_incidencia(self, incidencia_id: str) -> dict | None:
        try:
            return self._incidencias.read_item(incidencia_id, partition_key=incidencia_id)
        except exceptions.CosmosResourceNotFoundError:
            return None

    def guardar_incidencia(self, doc: dict) -> None:
        self._incidencias.upsert_item(doc)

    def listar_incidencias_zona(self, zona_id: str) -> list[dict]:
        return list(self._incidencias.query_items(
            "SELECT c.estado, c.prioridad, c.incidenteColectivoId FROM c WHERE c.zonaId = @z",
            parameters=[{"name": "@z", "value": zona_id}],
            enable_cross_partition_query=True,
        ))

    # -- zonas
    def modificar_zona(self, zona_id: str, fn: Callable[[dict], dict]) -> dict:
        """Lee, aplica `fn` y escribe con control optimista de concurrencia (etag)."""
        for _ in range(REINTENTOS_ETAG):
            try:
                doc = self._zonas.read_item(zona_id, partition_key=zona_id)
            except exceptions.CosmosResourceNotFoundError:
                try:
                    return self._zonas.create_item(fn({"id": zona_id, "zonaId": zona_id}))
                except exceptions.CosmosResourceExistsError:
                    continue  # otro proceso la creó primero
            try:
                return self._zonas.replace_item(
                    zona_id, fn(doc), etag=doc["_etag"],
                    match_condition=MatchConditions.IfNotModified)
            except exceptions.CosmosAccessConditionFailedError:
                continue
        raise RuntimeError(f"Conflicto de concurrencia persistente en la zona {zona_id}")

    def obtener_zona(self, zona_id: str) -> dict | None:
        try:
            return self._zonas.read_item(zona_id, partition_key=zona_id)
        except exceptions.CosmosResourceNotFoundError:
            return None

    def listar_zonas(self) -> list[dict]:
        return list(self._zonas.query_items(
            "SELECT * FROM c", enable_cross_partition_query=True))
