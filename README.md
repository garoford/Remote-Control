# Remote-Control

Túnel web de terminal (ttyd + Cloudflare Quick Tunnel) con app GTK4/Adwaita.

## Instalar

```bash
curl -fsSL https://raw.githubusercontent.com/garoford/Remote-Control/refs/heads/main/install.sh | bash
```

Instala `remote-control` en `~/.local/bin`, el launcher en el menú, y un clone git en `~/.local/share/remote-control/src`.

Requisitos: Fedora (o equivalente) con `git`, `python3`, `rsync`, GTK4, libadwaita. El script instala los paquetes que falten con `dnf` si hace falta. Asegúrate de tener `~/.local/bin` en el PATH.

## Dominio propio

En vez de una URL `….trycloudflare.com` al azar, Remote Control puede usar un nombre tuyo (`rc.midominio.com`) de **name.com**, **GoDaddy** o **Namecheap**, con la API key del proveedor. Se configura en la tarjeta *Dominio propio* de la ventana, o por SSH:

```bash
remote-control domain set --provider namecom --host rc.midominio.com --user TU_USUARIO   # pide el token
remote-control domain set --provider godaddy --host rc.midominio.com                     # pide key y secret
remote-control domain set --provider namecheap --host rc.midominio.com --user TU_USUARIO # pide la API key
remote-control domain show | check | off
```

Se aplica al encender el túnel. El modo *Automático* elige solo:

| Dónde corre | Modo | Qué hace |
| --- | --- | --- |
| Servidor/VPS (la IP pública está en su propia interfaz) | **Directo** | Registro A → esta máquina, certificado Let's Encrypt por DNS-01 (sin abrir el puerto 80), HTTPS en el 443 (8443 si no se puede abrir el 443). Sin Cloudflare: menos latencia y sin sus límites. Sigue la IP si cambia y renueva el certificado solo. |
| PC con el puerto 443 abierto en el router (443 → esta PC:8443) | **Directo** | Lo mismo que un servidor: HTTPS en tu dominio, sin redirección. Se comprueba desde fuera (ifconfig.co) con la app escuchando, así que solo se ofrece si de verdad llega. |
| PC en casa sin puerto abierto (o con CGNAT) | **Túnel** | Sigue el túnel de Cloudflare y el nombre redirige (`http://`, 302) a su URL de cada arranque. |

Qué pide cada proveedor:

- **name.com**: usuario y API token (Account → Settings → Security → API Token). [Docs](https://docs.name.com).
- **GoDaddy**: un token personal (PAT) con permisos de Domains & DNS, o la key + secret clásicos. Para el modo túnel, también tu número de cliente (las redirecciones de GoDaddy lo piden). [Docs](https://developer.godaddy.com/docs/references/rest/domains/v1).
- **Namecheap**: usuario y API key (Profile → Tools → API Access). Namecheap solo acepta llamadas desde IPs de su lista blanca: agrega ahí la IP pública de la máquina, y el dominio debe usar sus DNS (BasicDNS). Namecheap habilita la API solo a cuentas que cumplen sus requisitos (saldo o cantidad de dominios). [Docs](https://www.namecheap.com/support/api/intro/).

La clave queda en `~/.config/remote-control/domain.json` (permisos 600) y el certificado en `~/.config/remote-control/tls/`. Usa un subdominio dedicado: Remote Control reemplaza sus registros A/AAAA/CNAME y su redirección.

## Multi: espacios de trabajo aislados

Cada espacio de trabajo es un contenedor de Podman (rootless) con su propio Remote Control, túnel, URL y contraseña. Adentro es un Linux completo (systemd, `sudo` sin contraseña, `dnf`/`apt`), y su disco crece según lo que guarda, sin tamaño fijo.

```bash
remote-control multi create backend --base fedora   # o ubuntu; la primera vez arma la imagen
remote-control multi tunnel backend on              # imprime su URL pública
remote-control multi shell backend                  # una terminal adentro
remote-control multi list | stop | start | rm | rename | password | update | autostart
```

**Dominio propio por contenedor:** con la cuenta del proveedor configurada (⚙ en la tarjeta "Dominio propio"), cada contenedor puede tener su nombre: `backend.midominio.com`, `ia.midominio.com`… Un router en esta máquina (`remote-control router`, servicio de usuario `remote-control-router`) recibe el HTTPS en el 443 (8443 con el router de casa reenviando el 443), elige por el nombre pedido (SNI) y lo manda al contenedor. Los certificados (Let's Encrypt, uno por nombre) y la API key se quedan en esta máquina; el contenedor no usa cloudflared. Solo se ofrece si el puerto 443 llega a esta máquina, y nunca toma un nombre que ya tenga registros ajenos.

```bash
remote-control multi domain backend backend   # backend.midominio.com
remote-control multi domain backend off
```

Lo que depende del escritorio, resuelto adentro: el sonido (herdr, CLIs) sale por un PipeWire propio de cada contenedor hacia las páginas web; copiar va por el navegador (OSC 52); "autostart" enciende el contenedor con su túnel al iniciar sesión. `update` copia adentro el Remote Control de esta máquina sin recrear el contenedor.

## Actualizaciones

La copia instalada se actualiza sola cuando `main` tiene un commit nuevo:

- al abrir la app
- al volver a enfocar la ventana (como un editor)
- cada 3 minutos (timer systemd de usuario), aunque la app esté cerrada

No corta un túnel que ya esté en marcha. El código nuevo aplica al siguiente arranque.

Para desactivar el auto-update:

```bash
systemctl --user disable --now remote-control-update.timer
```

Log: `~/.local/share/remote-control/update.log`

## Desinstalar

```bash
remote-control uninstall               # pide escribir "si"; --keep-data conserva contraseña, cuenta de dominios y perfiles
curl -fsSL https://raw.githubusercontent.com/garoford/Remote-Control/refs/heads/main/install.sh | bash -s -- --uninstall
```

Quita la app, el lanzador, el auto-update, el router, la sesión tmux del túnel, los contenedores multi (y su imagen), los registros DNS que creó Remote Control, sus avisos en los CLAUDE.md/AGENTS.md de cada perfil y las líneas que agregó a `~/.bashrc`/`~/.zshrc`. Se quedan los paquetes del sistema, herdr y `~/Remote Control` (tus archivos pegados). Hay que correrlo desde fuera de Remote Control (SSH o la PC), no desde su terminal web.

## Desarrollo

Desde un clone del repo:

```bash
./app/install.sh
# o
./install.sh
```
