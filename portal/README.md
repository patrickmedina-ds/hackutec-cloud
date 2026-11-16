# Portal web · Red TeleCorp

SPA en Vite + React + TypeScript (sin Amplify). Consume la API de `contratos/openapi.yaml`, autentica con Cognito (código + PKCE hecho a mano) y tiene un modo demostración con datos de ejemplo.

La página inicial es **pública** (sin login), con dos pestañas: **Reportar falla** (DNI o número de servicio; mensajes para 404 sin contrato, 409 varios servicios con elección del servicio y 403 suspendido) y **Consultar mis reportes** (DNI + número de servicio, con línea de tiempo). Usa `POST /reportes` y `POST /reportes/consulta` sin token (`crearApiPublica`). El botón **Acceso operadores** lleva al login de Cognito; con sesión de operador se ve el Panel de operación. "Crear cuenta" solo aparece ahí, para el personal.

## Modo demostración

```bash
cd portal
npm install
npm run dev          # http://localhost:5173/?demo=1
```

- Con `apiUrl` vacío en `public/config.json` (valor por defecto del repo) el portal arranca siempre en demo.
- Con una API real configurada, añade `?demo=1` a la URL para forzar la demo.
- Una banda ámbar fija marca "MODO DEMOSTRACIÓN"; los datos viven en memoria y se pierden al recargar.
- El selector **Vista** del encabezado alterna entre la página pública de clientes y el operador. Clientes de ejemplo: DNI `70000001` (`SRV-00000001`), `70000002` (dos servicios) y `70000004` (suspendido). Datos: zonas de Lima, incidencias en los 5 estados, un colectivo en San Juan de Lurigancho con 12 reportes hijos, antenas caídas (`ANT-SJL-03`, `ANT-LOL-02`), saturadas e inhabilitada. Las transiciones inválidas devuelven un 409 como la API real.

## Configuración en tiempo de ejecución

El portal lee `/config.json` al arrancar (sin caché), así el mismo build sirve para cualquier entorno. El despliegue lo reemplaza; ver `public/config.example.json`:

| Campo | Ejemplo |
|---|---|
| `apiUrl` | `https://abc123.execute-api.us-east-1.amazonaws.com/dev` |
| `region` | `us-east-1` |
| `cognitoDomain` | `telecorp-dev-123.auth.us-east-1.amazoncognito.com` (con o sin `https://`) |
| `clientId` | ID del app client del User Pool (cliente público, sin secreto) |
| `redirectUri` | URL de CloudFront con `/` final; debe estar en las Callback URLs y Sign-out URLs del app client |

El app client debe tener habilitado el flujo *authorization code grant* y los scopes `openid email profile`. Se envía el **ID token** en `Authorization` (sin prefijo `Bearer`, como indica el contrato). El rol sale del claim `cognito:groups`: `operadores` es operador; cualquier otro caso es usuario. La sesión se guarda en `sessionStorage` y se descarta al vencer el token.

## Desarrollo

```bash
npm test             # Vitest: cliente API (fetch simulado), transiciones, PKCE, sesión, validación, demo
npm run build        # tsc --noEmit + vite build -> dist/
npm run preview
```

Estructura: `src/api.ts` (cliente), `src/demo.ts` (misma interfaz `Api` en memoria), `src/publico.ts` (validación, mensajes y demo de la API pública), `src/PortalClientes.tsx` (vistas públicas), `src/pkce.ts` + `src/auth.ts` (OIDC), `src/transiciones.ts` (réplica de `TRANSICIONES` de `telecorp_comun/estados.py`; si el backend cambia, actualizar ahí y en su prueba).

## Despliegue

El hosting es un bucket S3 privado detrás de CloudFront (lo crea el stack raíz; ver sus salidas).

```bash
npm run build
cp config.produccion.json dist/config.json     # tu config real (no se versiona)
aws s3 sync dist/ s3://<bucket-portal>/ --delete --cache-control "public,max-age=300"
aws s3 cp dist/config.json s3://<bucket-portal>/config.json --cache-control "no-store"
aws cloudfront create-invalidation --distribution-id <ID> --paths "/index.html" "/config.json" "/assets/*"
```

CloudFront debe devolver `index.html` para 403/404 si se usa una ruta distinta de `/` (hoy la SPA solo usa `/` y query string).
