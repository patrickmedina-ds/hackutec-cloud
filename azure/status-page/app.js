(function () {
  "use strict";

  var ETIQUETAS = {
    normal: "Normal",
    en_evaluacion: "En evaluación",
    incidencia_identificada: "Incidencia identificada",
    restablecido: "Restablecido"
  };
  var PESO = { incidencia_identificada: 0, en_evaluacion: 1, restablecido: 2, normal: 3 };
  var INTERVALO_MS = 60000;

  var params = new URLSearchParams(window.location.search);
  var base = (params.get("api") || window.TELECORP_API || "/api").replace(/\/+$/, "");

  var $zonas = document.getElementById("zonas");
  var $resumen = document.getElementById("resumen");
  var $hora = document.getElementById("hora");
  var $error = document.getElementById("error");

  function el(tag, clase, texto) {
    var n = document.createElement(tag);
    if (clase) n.className = clase;
    if (texto !== undefined) n.textContent = texto;
    return n;
  }

  function detalle(z) {
    var partes = [];
    if (z.totalAbiertas) {
      partes.push(z.totalAbiertas + (z.totalAbiertas === 1 ? " reporte abierto" : " reportes abiertos"));
    }
    if (z.colectivoActivo) partes.push("incidencia colectiva activa");
    if (z.antenasDegradadas && z.antenasDegradadas.length) {
      partes.push(z.antenasDegradadas.length + (z.antenasDegradadas.length === 1 ? " antena afectada" : " antenas afectadas"));
    }
    return partes.length ? partes.join(" · ") : "Sin reportes abiertos";
  }

  function pintar(datos) {
    var zonas = (datos.zonas || []).slice().sort(function (a, b) {
      var d = (PESO[a.estado] !== undefined ? PESO[a.estado] : 9) - (PESO[b.estado] !== undefined ? PESO[b.estado] : 9);
      return d || String(a.zonaId).localeCompare(String(b.zonaId));
    });
    $zonas.textContent = "";
    var afectadas = 0;
    zonas.forEach(function (z) {
      if (z.estado === "en_evaluacion" || z.estado === "incidencia_identificada") afectadas++;
      var li = el("li", "zona estado-" + z.estado);
      var cab = el("div", "zona-cab");
      cab.appendChild(el("span", "zona-nombre", z.nombre || z.zonaId));
      cab.appendChild(el("span", "etiqueta", ETIQUETAS[z.estado] || z.estado));
      li.appendChild(cab);
      li.appendChild(el("p", "zona-detalle", detalle(z)));
      $zonas.appendChild(li);
    });
    if (!zonas.length) {
      $resumen.textContent = "Todavía no hay zonas con información.";
      $resumen.className = "resumen";
    } else if (afectadas) {
      $resumen.textContent = afectadas + (afectadas === 1 ? " zona con novedades" : " zonas con novedades");
      $resumen.className = "resumen alerta";
    } else {
      $resumen.textContent = "Todos los servicios funcionan con normalidad";
      $resumen.className = "resumen ok";
    }
    var fecha = datos.generadoEn ? new Date(datos.generadoEn) : new Date();
    $hora.textContent = fecha.toLocaleString("es-PE", { dateStyle: "medium", timeStyle: "medium" });
    $hora.setAttribute("datetime", fecha.toISOString());
    $error.hidden = true;
  }

  function cargar() {
    return fetch(base + "/zonas", { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(pintar)
      .catch(function () {
        $error.textContent = "No pudimos actualizar el estado. Mostramos la última información disponible; reintentando…";
        $error.hidden = false;
        if ($resumen.textContent === "Cargando…") $resumen.textContent = "Estado no disponible por ahora";
      });
  }

  document.getElementById("recargar").addEventListener("click", cargar);
  cargar();
  setInterval(cargar, INTERVALO_MS);
})();
