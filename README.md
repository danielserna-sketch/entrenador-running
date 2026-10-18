# Entrenador de running con IA

Agente que actúa como entrenador personal de running:

- **Garmin Connect**: lee actividades, sueño, HRV, Body Battery y readiness; crea y agenda entrenamientos estructurados en tu calendario (llegan al reloj); carga tus zonas de FC.
- **WhatsApp**: conversas con él (Meta WhatsApp Cloud API).
- **Metodología "Corriendo conCiencia" (Andrés Ramírez)**: test de 20 min (pace×1.05, FC×0.95), zonas Joe Friel, ciclos Base → Construcción → Específico, cumplimiento por colores sobre TSS, frecuencia según nivel y cadencia con metrónomo (+2-3 rpm). Ver [`correr_conciencia.md`](src/coach/methodology/correr_conciencia.md) y [`rules.py`](src/coach/methodology/rules.py). Lo que el método no publica está marcado como `SUPUESTO`.
- **Plan personalizado**: diagnóstico, macrociclo determinista, semana generada por el LLM y revisada por un validador de reglas, y publicación en Garmin.
- **Reporte HTML por correo** (Amazon SES): se envía al crear el plan, cada domingo y en cada replanificación. Ver el [ejemplo](docs/ejemplo_reporte_plan.html).
- **Autonomía**: análisis después de cada actividad, resumen a las 6:15, revisión del domingo a las 18:00 (genera la semana siguiente) y alertas (ACWR, HRV, FC en reposo, sueño, sesiones perdidas, dolor).
- **LLM multiproveedor**: `LLM_PROVIDER=anthropic|openai`.

## Arquitectura

```
WhatsApp → API Gateway → λ webhook (firma, allowlist, dedupe) → SQS FIFO → λ agent_worker → LLM + tools
EventBridge Scheduler → λ garmin_sync (cada 30 min) → análisis post-actividad
                      → λ proactive_jobs (brief, revisión semanal, alertas)
DynamoDB · S3 (JSON crudo + reportes) · Secrets Manager · SES · CloudWatch → SNS (email)
```

Código en [`src/coach`](src/coach): `agent/` (bucle y herramientas), `llm/` (Anthropic/OpenAI), `garmin/`, `whatsapp/`, `analytics/` (TSS, zonas, colores, cadencia, alertas), `planning/`, `reports/` y `handlers/` (Lambdas). Infraestructura en [`infra/`](infra/stacks/coach_stack.py).

## Puesta en marcha

### 0. Requisitos
- Cuenta de AWS con credenciales configuradas, Docker y Node (para `npx aws-cdk`).
- [uv](https://docs.astral.sh/uv/).
- API key de Anthropic (console.anthropic.com) y/o de OpenAI. La suscripción Claude Pro **no** da acceso a la API.
- App en [Meta for Developers](https://developers.facebook.com) con WhatsApp: número de negocio, *phone number ID*, token permanente de un System User y *app secret*.

### 1. Instalar y probar
```bash
uv sync --all-groups
```
```bash
uv run pytest
```

### 2. Configurar y desplegar
Edita `cdk.json` → `context.coach`: `allowed_phone` (tu número sin "+", ej. 573001234567), `report_email`, `sender_email`, `llm_provider`, etc.
```bash
npx aws-cdk@2 bootstrap
```
```bash
npx aws-cdk@2 deploy
```
Al terminar:
1. **SES**: confirma el correo de verificación que llega a `sender_email`. En sandbox, verifica también `report_email` si es distinto.
2. **SNS**: confirma la suscripción a las alarmas.
3. **Secretos** (Secrets Manager → reemplaza `REEMPLAZAR`):
   - `coach/whatsapp`: `access_token`, `app_secret`, `verify_token` (lo inventas tú) y `phone_number_id`.
   - `coach/llm`: `anthropic_api_key` y/o `openai_api_key`.
4. **Garmin** (una vez, desde tu computador; admite MFA):
   ```bash
   uv run python scripts/garmin_login.py
   ```
5. **Webhook de WhatsApp**: en Meta → WhatsApp → Configuración, usa la salida `WebhookUrl` y tu `verify_token`, y suscríbete al campo `messages`.
6. **Plantilla para mensajes proactivos**: crea en WhatsApp Manager una plantilla *Utility* llamada `coach_update`, idioma `es`, con un parámetro. Por ejemplo: *"Tu entrenador tiene novedades: {{1}}. Respóndeme para ver el detalle."* Se usa cuando pasaron más de 24 h desde tu último mensaje.

### 3. Primer uso
Escríbele por WhatsApp. Te hará el onboarding (carrera objetivo, fecha, marcas, días disponibles y lesiones), creará el plan, lo publicará en Garmin y te enviará el reporte por correo. La primera semana incluye el test de 20 minutos. Cuando lo hagas, el agente recalcula tus zonas, las carga en Garmin y te envía una versión nueva del reporte.

Ejemplos: "¿cómo estuvo mi semana?", "hoy dormí mal, ¿qué hago con las series?", "mueve el fondo al sábado", "envíame el plan".

## Cambiar de proveedor de IA
En `cdk.json`: `llm_provider` (`anthropic` u `openai`), opcionalmente `llm_provider_proactive` (otro proveedor para los mensajes automáticos) y `llm_model_main` / `llm_model_fast`. Luego vuelve a desplegar. Por defecto: Anthropic con `claude-sonnet-5-5` (conversación y plan) y `claude-haiku-5-5`; OpenAI con `gpt-5` / `gpt-5-mini`.

## Costos estimados (1 usuario)
Lambda/SQS/DynamoDB/API Gateway: ~$0-2/mes. Secrets Manager: ~$1.2/mes. LLM: ~$3-15/mes. WhatsApp: gratis dentro de la ventana de 24 h; las plantillas utility cuestan centavos. Hay un presupuesto de AWS con alerta (`monthly_budget_usd`). Pon también un límite de gasto en la consola de Anthropic/OpenAI.

## Riesgos conocidos
- `garminconnect` es una librería no oficial: Garmin puede cambiar su login. Si la sesión expira, llega una alerta por WhatsApp y por correo; vuelve a correr `scripts/garmin_login.py`. El acceso a Garmin está detrás de la interfaz `GarminProvider`, así que migrar a la API oficial no toca el resto.
- Las zonas de FC de Garmin son 5: Z5a-Z5c de Friel se agrupan en la zona 5 del reloj. Los workouts usan rangos de pace exactos.
