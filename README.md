# Remote-Control

Túnel web de terminal (ttyd + Cloudflare Quick Tunnel) con app GTK4/Adwaita.

## Instalar

```bash
curl -fsSL https://raw.githubusercontent.com/garoford/Remote-Control/main/install.sh | bash
```

Instala `remote-control` en `~/.local/bin`, el launcher en el menú, y un clone git en `~/.local/share/remote-control/src`.

Requisitos: Fedora (o equivalente) con `git`, `python3`, `rsync`, GTK4, libadwaita. El script instala los paquetes que falten con `dnf` si hace falta. Asegúrate de tener `~/.local/bin` en el PATH.

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
