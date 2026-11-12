# Linux Tablet — CachyOS / KDE Plasma 6.8 (русская инструкция)

**Языки:** [English guide](README.md) · Русский

**Подтверждённая конфигурация:** 24 октября 2026, трансформер Intel Celeron N4020, экран 1366×768 (`eDP-1`), CachyOS LTS 6.18.50, Plasma 6.8.1, Wayland.

Это **документация одной успешной конфигурации конкретного устройства**, а не универсальный установщик всех планшетов. Разные ревизии Silead требуют другой прошивки и калибровки.

## Что подтверждено на устройстве

- Silead MSSL1680: исходная прошивка 38 544 байта, MD5 `86e979b61f02ebad6b20ac66a7708c29`; фильтр `scripts/silead-unfold.py` v10 (уже находится в корне репозитория).
- Правый клик длительным касанием: `scripts/silead-longpress.py` (0,75 с).
- Два датчика Kionix: `KIOX020A:00` и `KIOX010A:00`.
- Переключение шарнира: **190° и выше — планшет**, ниже — ноутбук; задержка 0,70 секунды.
- Управление физической клавиатурой и HTIX5288 touchpad через KWin D-Bus (без X11 `xinput`).
- Автоповорот в KDE Wayland через `kscreen-doctor` и `monitor-sensor`.
- Родная Plasma Keyboard: отдельный значок в системном лотке **только в планшетном режиме**; при раскрытом ноутбуке экранная клавиатура выключается.
- Waydroid: три общие папки `Download/Documents/Pictures` через вложенные `LinuxShared`; обмен в обе стороны подтверждён, а после перезагрузки Android снова увидел все три маркера.

### Структура файлов

- `scripts/linux-tablet-hinge-kde.py`: считывание угла и включение/выключение встроенной клавиатуры и тачпада; по умолчанию **только наблюдение**.
- `scripts/linux-tablet-autorotate-kde.py`: независимый поворот; читает математику шарнира из установленного файла.
- `scripts/linux-tablet-keyboard-tray.py`: индикатор экранной клавиатуры с ручным переключением.
- `tools/install-waydroid-folders-cachyos-v3.sh`: служба повторного подключения каталогов после запуска Android.
- `tools/backup-linux-tablet-cachyos-v2-fixed.sh`: приватная копия настроек, скриптов, ACL и диагностики.

> Файлы для CachyOS не заменяют существующий установщик Linux Mint в корне репозитория. **Не запускайте `install.sh` из корня как установщик CachyOS**: в нём используются `apt`, XRandR и Cinnamon.

## Зависимости

Для этого конкретного устройства ранее использовались:
```bash
sudo pacman -S --needed python-evdev python-pyqt6 qt6-tools iio-sensor-proxy plasma-keyboard acl git
```

Требуются также systemd, `busctl`, `kscreen-doctor`, `monitor-sensor`, `uinput`, Wayland-сеанс Plasma, настроенный Waydroid. Пакет и настройки Waydroid устанавливаются отдельно.

## Перенос KDE-контроллеров на аналогичную систему

Только после проверки датчиков, путей устройств и создания резервной копии:

```bash
mkdir -p ~/.local/bin ~/.config/autostart
install -m755 platforms/cachyos/scripts/linux-tablet-hinge-kde.py ~/.local/bin/linux-tablet-hinge-kde
install -m755 platforms/cachyos/scripts/linux-tablet-autorotate-kde.py ~/.local/bin/linux-tablet-autorotate-kde
install -m755 platforms/cachyos/scripts/linux-tablet-keyboard-tray.py ~/.local/bin/linux-tablet-keyboard-tray
```

Безопасные проверки:
```bash
~/.local/bin/linux-tablet-hinge-kde
~/.local/bin/linux-tablet-autorotate-kde
~/.local/bin/linux-tablet-keyboard-tray --check
```
Параметр `--apply` у контроллеров шарнира и поворота **меняет настройки системы**. Прежде чем использовать его, убедитесь, что угол 190° и карта поворота соответствуют именно вашему устройству.

Подтверждённая карта поворотов: `bottom-up→normal`, `normal→inverted`, `left-up→left`, `right-up→right`. В режиме ноутбука экран принудительно возвращается в `normal`.

В подтверждённой установке KDE контроллеры запускаются при входе в Plasma из файлов `~/.config/autostart`, команды:
```ini
Exec=/home/USER/.local/bin/linux-tablet-hinge-kde --apply
Exec=/home/USER/.local/bin/linux-tablet-autorotate-kde --apply
Exec=/home/USER/.local/bin/linux-tablet-keyboard-tray
```
Здесь это **образцы трёх отдельных desktop-записей**, а не строки для одного файла; замените `USER` и используйте стандартные секции `[Desktop Entry]`. Существующие рабочие файлы не перезаписывайте без проверки.

## Waydroid: общие папки

В исходной успешной конфигурации используются пользовательские каталоги **с русскими именами**: `Загрузки`, `Документы`, `Изображения`. Другие названия надо адаптировать в установщике и наблюдателе. В Android:
- `/sdcard/Download/LinuxShared`
- `/sdcard/Documents/LinuxShared`
- `/sdcard/Pictures/LinuxShared`

Главная находка: прямой `mount --bind` поверх `/data/media/0/Download` отображался в Linux, но **не в Android FUSE**. Вложенная папка `LinuxShared` заработала. После перезагрузки v3 повторно переподключила три каталога, и Android увидел файлы.

```bash
bash platforms/cachyos/tools/install-waydroid-folders-cachyos-v3.sh
systemctl is-active waydroid-bind-folders
sudo journalctl -u waydroid-bind-folders -n 30 --no-pager
for d in Download Documents Pictures; do
  sudo waydroid shell -- cat "/sdcard/$d/LinuxShared/.linux-tablet-waydroid-v3.check"
done
```

### Разрешения и ограничения безопасности

Linux-каталоги с владельцем UID 1000 и правами 755 оказались доступны Android только для чтения. Для эксперимента Android создал файл с UID **10141**; ACL для UID 1000 и 10141 обеспечили двусторонний обмен. **Android UID приложений могут меняться и отличаться** на другой установке. Не используйте рекурсивный `chmod 777` на домашних каталогах. Настраивайте разрешения выборочно и проверяйте запись из конкретного приложения.

Установщик v3 **сам ACL не устанавливает**: он рассчитывает на уже настроенные права и тестирует наличие файлов через Android FUSE. Когда Android долго не видит каталог, служба может повторять попытки переподключения — проверьте журналы, прежде чем копировать важные файлы. Сам Waydroid и данные приложений установщик не переустанавливает.

## Сохранение системы

На проверенном устройстве был создан архив `LinuxTablet-CachyOS-20261024-180331.tar.gz` с успешной проверкой SHA-256, и снимок Snapper **№37** с описанием `Linux Tablet CachyOS GOLD - 24.10.2026`.

```bash
bash platforms/cachyos/tools/backup-linux-tablet-cachyos-v2-fixed.sh
```

Сохраняйте полученный архив **локально или на собственный внешний диск**, не публикуйте его на GitHub: в нём могут быть пользовательские настройки, локальные пути, ACL и сведения о системе. Архив не является полным образом диска и не содержит резервную копию Android-приложений. Snapper для корневого тома может не включать отдельный Btrfs `@home`.

## Статус публикации

Код опубликован как исходники подтверждённой установки на **одном** N4020. Отдельная чистая установка из этой документации без ручных настроек пока **не проверялась**. Существующий PR по Linux Mint и рабочие скрипты исходной ветки оставлены без изменений.

Совместный проект пользователя планшета и ChatGPT. Практические испытания и окончательное подтверждение функций выполнены на реальном устройстве его владельцем.
