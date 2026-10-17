# ML Publisher

Aplicación Django para preparar borradores de productos destinados a Mercado Libre Argentina. Permite administrar borradores, imágenes, conectar una cuenta y consultar metadatos; **no crea, modifica ni publica productos reales**.

## Estructura

- `config/`: configuración Django y URLs globales.
- `drafts/`: dominio de borradores, formularios, vistas, UI, servicios y pruebas.
- `drafts/services/mercado_libre_client.py`: cliente HTTP aislado para OAuth y metadatos de sólo lectura.
- `drafts/services/connections.py`: ciclo de vida de credenciales y renovación segura.

## Ejecutar con Docker

1. Copiá la configuración: `cp .env.example .env`.
2. Reemplazá los secretos de desarrollo en `.env`.
3. Iniciá los servicios: `docker compose up --build`.
4. Abrí `http://localhost:8000`.

La base PostgreSQL queda persistida en el volumen `postgres_data`. Para detener: `docker compose down`.

## Desarrollo local y pruebas

Creá un entorno virtual e instalá dependencias: `python -m venv .venv && .venv/bin/pip install -r requirements.txt`.

Para usar PostgreSQL local, cargá las variables de `.env` y ejecutá `python manage.py migrate` seguido de `python manage.py runserver`.

Para correr el conjunto de pruebas sin PostgreSQL, Django puede usar SQLite temporal:

```sh
DJANGO_DB_ENGINE=django.db.backends.sqlite3 POSTGRES_DB=:memory: python manage.py test
```

## Seguridad y límites actuales

- Formularios POST protegidos con CSRF; cookies HTTP-only, `X-Frame-Options: DENY` y `nosniff`.
- Las imágenes se verifican con Pillow: sólo JPG/JPEG y PNG reales, entre 500 × 500 y 1920 × 1920 px, hasta 10 MB por defecto (`MAX_UPLOAD_SIZE`). No se convierten ni se transfieren durante el preflight.
- `.env`, archivos de medios y artefactos locales están excluidos de Git.
- OAuth usa `state` de un solo uso, PKCE y un callback con vencimiento. Los access y refresh tokens se cifran con Fernet usando `MERCADO_LIBRE_ENCRYPTION_KEY`, que nunca debe guardarse en el repositorio.
- La renovación reemplaza el refresh token almacenado. Si falla, se informa que debe reconectarse la cuenta.
- El preflight comercial sólo consulta perfil, predictor, categoría, atributos y tipos de publicación disponibles. Nunca renueva tokens: informa el bloqueo si la autorización no alcanza.
- Para sellers con `user_product_seller`, el borrador separa `family_name`, `domain_id`, atributos de familia/producto y condición de venta. El título local es sólo referencia y no figura como `title` en la vista previa de User Products.
- Los atributos condicionales, canales, envío, transferencia de imágenes y el contrato final de creación quedan explícitamente pendientes cuando no existe un recurso GET que los confirme.
- No se habilitó ningún endpoint de escritura comercial. El único cliente de escritura existente continúa protegido para ensayos con usuario de test y permanece deshabilitado por defecto.

## Registrar la aplicación en Mercado Libre

La guía completa, incluyendo HTTPS, callback exacto, PKCE, variables y usuario vendedor de prueba, está en [docs/mercado-libre-setup.md](docs/mercado-libre-setup.md).

## Smoke test manual de sólo lectura

1. Con una cuenta ya conectada y token vigente, abrí **Diagnóstico**.
2. Opcionalmente ingresá un título y una categoría `MLA...` para comprobar el predictor y los atributos; la pantalla sólo realiza GET.
3. Abrí un borrador y elegí **Validar publicación**. Revisá bloqueos, advertencias, pendientes y la vista previa conceptual.
4. Si el token requiere renovación, el diagnóstico lo informará sin hacer POST; reconectá la cuenta mediante el flujo OAuth existente antes de repetirlo.

El diagnóstico detecta el tag `user_product_seller` desde el perfil, pero la ausencia del tag se informa como flujo indeterminado y no habilita automáticamente un flujo clásico. Para un seller comercial con ese tag, **Validar producto** consulta tipos disponibles para la categoría elegida y genera una vista de las entidades User Product y condición de venta, sin enviar payloads.

## Ensayo de escritura con usuario test

La escritura está deshabilitada por defecto. Para un ensayo manual, autorizá mediante OAuth un usuario MLA con tag `test_user`, configurá su ID exacto en `MERCADO_LIBRE_TEST_SELLER_ID` y habilitá temporalmente `MERCADO_LIBRE_ENABLE_TEST_WRITES=true`. Nunca uses una cuenta personal.

El borrador debe tener imágenes, superar el preflight y un título que comience con `Item de Prueba - Por favor, NO OFERTAR`. En el preflight elegí **Continuar a confirmación**, indicá los canales confirmados y marcá la confirmación explícita. El único tipo permitido por código es `free`, y debe estar disponible para ese seller/categoría; nunca se elige automáticamente un tipo de pago.

Cada ensayo crea una intención persistente antes de transferir imágenes. Un timeout queda como resultado desconocido y no puede reintentarse automáticamente: verificá el estado en Mercado Libre antes de cualquier recuperación. El ensayo para User Products está bloqueado: el título lo genera Mercado Libre y la aplicación no puede garantizar el prefijo obligatorio de publicaciones de test. Un flujo no determinado también queda bloqueado.
