# Configurar Mercado Libre OAuth

ML Publisher no registra aplicaciones, crea usuarios de prueba ni inicia OAuth automáticamente. Seguí estos pasos desde la cuenta de desarrollador correspondiente.

## 1. Registrar la aplicación

1. Abrí el panel de Mercado Libre Developers y creá o seleccioná la aplicación.
2. Copiá el **Client ID** y el **Client Secret** sólo a un gestor de secretos o a tu `.env` local.
3. Registrá una URI de callback estática que coincida exactamente con `MERCADO_LIBRE_REDIRECT_URI`, incluido esquema, host, ruta y barra final.
4. Usá HTTPS para la URI registrada. Para desarrollo local, exponé temporalmente el callback mediante un túnel HTTPS o usá un entorno de desarrollo HTTPS controlado; no registres un callback con parámetros variables.
5. Habilitá PKCE en la aplicación si está disponible. ML Publisher usa `state` y PKCE.

Si ya existe un volumen local de PostgreSQL, conservá las variables `POSTGRES_*` con las que se inicializó. Cambiarlas después puede impedir la conexión; no recrees ni elimines el volumen salvo que hayas decidido descartar explícitamente esos datos de desarrollo.

## 2. Configurar el entorno local

Copiá el ejemplo sin versionarlo:

```sh
cp .env.example .env
```

Completá estas variables sólo en `.env` o en el administrador de secretos del entorno:

Mientras `MERCADO_LIBRE_CLIENT_ID`, `MERCADO_LIBRE_CLIENT_SECRET`, `MERCADO_LIBRE_REDIRECT_URI` o `MERCADO_LIBRE_ENCRYPTION_KEY` estén vacíos, ML Publisher mantiene deshabilitado el inicio de OAuth. No existe un callback HTTP alternativo.

| Variable | Propósito |
| --- | --- |
| `MERCADO_LIBRE_CLIENT_ID` | Identificador público de la aplicación registrada. |
| `MERCADO_LIBRE_CLIENT_SECRET` | Secreto de la aplicación; nunca se versiona ni registra. |
| `MERCADO_LIBRE_REDIRECT_URI` | Callback HTTPS registrado exactamente en Developers. |
| `MERCADO_LIBRE_ENCRYPTION_KEY` | Clave Fernet externa para cifrar tokens en la base de datos. |
| `MERCADO_LIBRE_TIMEOUT` | Límite de segundos para solicitudes HTTP. |
| `MERCADO_LIBRE_ENABLE_TEST_WRITES` | Protección de escrituras; debe permanecer `false`. |
| `MERCADO_LIBRE_TEST_SELLER_ID` | ID exacto de seller test permitido; dejalo vacío mientras no haya ensayo autorizado. |

Generá la clave Fernet localmente y guardala sólo en `.env`:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 3. Crear y conectar un usuario de prueba MLA

1. Siguiendo la guía oficial, creá un usuario de prueba vendedor con `site_id` `MLA`. La operación de creación se realiza manualmente desde el flujo oficial y devuelve credenciales que Mercado Libre no vuelve a listar.
2. Conservá esas credenciales fuera del repositorio y nunca uses una cuenta personal para pruebas.
3. Iniciá sesión como ese seller test, abrí **Mercado Libre** en ML Publisher y autorizá la aplicación. El usuario debe ser administrador, no colaborador.
4. Abrí **Diagnóstico**. Con token vigente sólo hará consultas GET de perfil, sitio, tags, predictor y atributos.
5. El tag `test_user` confirma la cuenta de pruebas. El tag `user_product_seller` indica activación del modelo User Products; si no aparece, ML Publisher conserva el flujo como `undetermined` y bloquea escrituras.

## 4. Seguridad y límites

- Los tokens viajan por header y se cifran en reposo; nunca se imprimen en pantalla ni logs.
- Si expira el token durante un diagnóstico de sólo lectura, el diagnóstico no lo renueva: realizá una autorización/renovación manual posterior.
- No cambies `MERCADO_LIBRE_ENABLE_TEST_WRITES` ni configures `MERCADO_LIBRE_TEST_SELLER_ID` para esta etapa.
- User Products permanece bloqueado para publicaciones test hasta contar con un contrato oficial que garantice el título de prueba generado.
