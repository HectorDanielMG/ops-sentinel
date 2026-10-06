# Ops Sentinel

**Un panel ligero de confiabilidad para servicios web.** Supervisa disponibilidad, latencia, incidentes y objetivos SLO desde un mismo lugar.

Ops Sentinel es un proyecto de portafolio centrado en la operación de software confiable: comprobaciones de estado, seguimiento de incidentes, objetivos de nivel de servicio y despliegue con contenedores. Funciona localmente con Docker Compose e incluye endpoints de demostración, así que puedes explorar el panel sin cuentas externas ni claves de API.

## Funciones

- Comprueba endpoints HTTP registrados en un intervalo configurable.
- Guarda el tiempo de respuesta y el código HTTP en SQLite.
- Abre un incidente cuando falla un servicio y lo resuelve cuando se recupera.
- Muestra disponibilidad de las últimas 24 horas, latencia y cumplimiento del SLO por servicio.
- Publica métricas compatibles con Prometheus en `/metrics`.
- Incluye endpoints de demostración estables e intermitentes.
- Se despliega con Docker y conserva la base de datos en un volumen persistente.

## Inicio rápido

```bash
docker compose up --build
```

Abre [http://localhost:8000](http://localhost:8000). En el primer inicio se registran dos servicios de demostración: uno está siempre disponible y el otro alterna entre periodos de respuesta correcta y fallas para mostrar cómo se abren y resuelven incidentes.

### Ejecutarlo sin Docker

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn ops_sentinel.main:app --reload
```

## Agregar un servicio

Usa el botón **Agregar servicio** del panel o llama a la API:

```bash
curl -X POST http://localhost:8000/api/services \
  -H 'Content-Type: application/json' \
  -d '{"name":"API de pagos","url":"https://ejemplo.com/health","slo_target":99.9}'
```

El endpoint debe ser accesible desde el proceso de Ops Sentinel. Puedes cambiar el intervalo de comprobación con `CHECK_INTERVAL_SECONDS` (30 segundos por defecto), el tiempo máximo de espera con `REQUEST_TIMEOUT_SECONDS` (5 segundos por defecto) y la ubicación de SQLite con `DATABASE_PATH`.

## API

| Método | Endpoint | Función |
| --- | --- | --- |
| `GET` | `/api/summary` | Disponibilidad general e incidentes activos |
| `GET` | `/api/services` | Servicios, estado actual y datos SLO de las últimas 24 horas |
| `POST` | `/api/services` | Registrar un endpoint para monitoreo |
| `GET` | `/api/incidents` | Consultar incidentes recientes |
| `POST` | `/api/checks/run` | Ejecutar una comprobación de inmediato |
| `GET` | `/healthz` | Comprobar que el monitor está activo |
| `GET` | `/metrics` | Métricas en formato Prometheus |

La documentación interactiva de la API está en [`/docs`](http://localhost:8000/docs).

## Cómo se calcula la disponibilidad

La disponibilidad se calcula con las comprobaciones de las últimas 24 horas. Una respuesta HTTP entre 200 y 399 cuenta como exitosa. El indicador SLO compara esa disponibilidad medida con el objetivo configurado para el servicio. Este proyecto usa comprobaciones periódicas, no métricas ponderadas por volumen de solicitudes; está pensado para demostraciones y servicios de bajo tráfico.

## Arquitectura

```text
Panel web ──> FastAPI ──> SQLite
                   │
                   ├── comprobaciones HTTP periódicas ──> servicios monitoreados
                   └── /metrics ──> Prometheus
```

## Desarrollo

```bash
pip install -r requirements.txt
uvicorn ops_sentinel.main:app --reload
```

## Próximas mejoras

- Agregar notificaciones por correo, Telegram o webhook con manejo seguro de secretos.
- Configurar ventanas de mantenimiento y umbrales de alertas.
- Incorporar métricas de solicitudes para calcular SLO de producción.
- Añadir autenticación antes de exponer el panel fuera de una red confiable.

## Licencia

Licencia MIT. Consulta el archivo [LICENSE](LICENSE).
