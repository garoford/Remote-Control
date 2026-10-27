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
| PC en casa (detrás del router o CGNAT) | **Túnel** | Sigue el túnel de Cloudflare y el nombre redirige (302) a su URL de cada arranque. |

Qué pide cada proveedor:

- **name.com**: usuario y API token (Account → Settings → Security → API Token). [Docs](https://docs.name.com).
- **GoDaddy**: un token personal (PAT) con permisos de Domains & DNS, o la key + secret clásicos. Para el modo túnel, también tu número de cliente (las redirecciones de GoDaddy lo piden). [Docs](https://developer.godaddy.com/docs/references/rest/domains/v1).
- **Namecheap**: usuario y API key (Profile → Tools → API Access). Namecheap solo acepta llamadas desde IPs de su lista blanca: agrega ahí la IP pública de la máquina, y el dominio debe usar sus DNS (BasicDNS). Namecheap habilita la API solo a cuentas que cumplen sus requisitos (saldo o cantidad de dominios). [Docs](https://www.namecheap.com/support/api/intro/).

La clave queda en `~/.config/remote-control/domain.json` (permisos 600) y el certificado en `~/.config/remote-control/tls/`. Usa un subdominio dedicado: Remote Control reemplaza sus registros A/AAAA/CNAME y su redirección.

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

## Desarrollo

Desde un clone del repo:

```bash
./app/install.sh
# o
./install.sh
```
