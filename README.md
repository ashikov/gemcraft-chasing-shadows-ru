# GemCraft: Chasing Shadows — русификация

Неофициальный проект русской локализации для личного использования.
Правила для coding agents: [AGENTS.md](AGENTS.md).
**Повторный runtime-тест подтвердил русские подписи во всех четырёх разделах
меню и проверенные динамические подсказки.** После замечаний о форме «п», «б» и
«ю» локальные рецепты глифов исправлены; новый кандидат имеет статус
**READY FOR RUNTIME VERIFICATION** и ещё не проверен в игре. Этап 3 применяет
141 подтверждённую запись и одно уведомление с неподтверждённой достижимостью.
Общий статус — **PARTIAL** до проверки обновлённых глифов и остальных состояний.

Поддерживается только проверенный Steam SWF игры 1.0.6 (build 734216,
AppID 296490), определяемый **по SHA-256**, а не по имени или номеру версии:

```text
original: c8eadc03a7e607086998695f7696ec22dd90d7b7859e3d10cdfabf40fc7bbad5
prototype: 41bb1a40cf95f8b48a9829df130ee492f07c8dcb2539070b11c07fc181f592e9
```

## Структура и требования

- `build_prototype.py`, `swf_geometry.py`: существующие SWF/font/text инструменты;
  добавление DefineFont3 и замена единственного DefineText 3016.
- `addressed_localization.py`, `abc_patch.py`, `localization_fonts.py`:
  адресное применение выбранных локальных переводов и расширение встроенного шрифта.
- `install_patch.py`, `restore_game.py`: hash-guarded apply/restore и backup.
- `preview_typography.py`: необязательный офлайн просмотр контуров.
- `verify_experiment.py`: read-only проверка установленного патча.
- `extract_strings.py`, `inventory_strings.py`, `abc_inventory.py`: read-only
  инвентаризация статических/редактируемых SWF-текстов и ABC.
- `translation_catalog.py`, `localization_workspace.py`, `translations/`:
  проверенный пример, локальная двуязычная работа, конфликты и минимальная проекция.
- `tests/`, `.github/workflows/`, `docs/`: проверки и документация.
- `local/extraction/`, `backups/`, `output/`, `artifacts/`, `reference-fonts/`, старые отчёты и дампы:
  **только локально**, исключены из Git.

Нужны Python 3.11+, собственная установленная Steam-копия игры и доступ к её
файлам. Патчер использует только стандартную библиотеку. Тесты требуют pytest,
проверка стиля — Ruff. Для необязательных preview нужен Pillow и локальные
референсные TTF; сборка и тесты от них не зависят.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

## Подготовка и применение

Закройте игру перед изменением файлов. Ни один публикуемый инструмент её не
запускает. Для любого будущего GUI/runtime запуска требуется отдельное явное
согласие пользователя. Сначала используйте копии и проверьте результат.

Исходный SWF получают из собственной установки. Если патч уже установлен,
используйте проверенный оригинальный backup, а не изменённый SWF. Не скачивайте
игровые ресурсы из проекта: их здесь нет. Не переносите существующие backup и
манифесты: они содержат точные локальные пути.

```sh
# Укажите свой путь к оригиналу: установленному SWF либо его backup.
python build_prototype.py --source /path/to/original/gc-cs-steam.swf
# Результат: output/gc-cs-steam.ru.swf и static-validation.json.
# Только если установка оригинальная и вы намерены изменить её:
python install_patch.py apply --game-file /path/to/game/gc-cs-steam.swf
```

Путь к игре задаётся `--game-file` либо `GEMCRAFT_GAME_FILE`; изменяющие команды
не выбирают установку автоматически. `--candidate` и `--backup-dir` позволяют
задать другие локальные пути; backup должен быть вне каталога игры.
Сборка принимает `--output-dir` и отказывается перезаписывать исходник.

Apply заново проверяет структуру кандидата и SHA исходника, создаёт backup
эксклюзивно, сверяет его побайтово, записывает манифест и атомарно заменяет SWF.
Первый backup никогда не перезаписывается. Уже изменённый или неизвестный SWF
отклоняется. Повреждённый backup тоже блокирует установку.

При установленном патче новый apply запрещён: сначала проверьте старый
манифест и сохраните предыдущий кандидат, затем восстановите оригинал штатным
restore. Не подменяйте модифицированный SWF напрямую. Локальная последовательность
перехода и отката должна быть сначала проверена на изолированной копии.

## Восстановление и read-only проверка

Используйте манифест, выведенный apply. Откат не требует пересборки шрифта:

```sh
python restore_game.py --game-file /path/to/game/gc-cs-steam.swf \
  --manifest /path/to/backups/manifest-HASH.json
# Эквивалент: python install_patch.py restore с теми же аргументами.
python verify_experiment.py --game-file /path/to/game/gc-cs-steam.swf \
  --backup-dir /path/to/backups
```

Restore проверяет оригинальный backup по размеру и SHA, разрешает замену только
известного установленного патча; повторный откат безопасен. После обновления
Steam неизвестный файл не перезаписывается. Не редактируйте манифест вручную.
Read-only verifier опционально принимает `--inventory file-inventory.json`:
тогда проверяет также неизменность остальных файлов по локальной инвентаризации.
Без inventory проверяет только SWF и backup. Это не runtime-проверка.

## Проверки

```sh
python -m pytest -q -m 'not local'
python -m ruff check .
python translation_catalog.py translations/ru.json
# Локальная интеграция: читает оригинал, пишет только во временные каталоги.
GEMCRAFT_ORIGINAL=/path/to/original/gc-cs-steam.swf python -m pytest -q -m local
```

Локальные тесты также находят существующий backup в `backups/`. При его
отсутствии проверки, требующие оригинала, пропускаются. GitHub Actions выполняет
только синтетические тесты, lint, компиляцию и проверку каталога, без игры.

## Ограничения и дальнейшая работа

Проверенный рецепт заголовка сохранён без изменения. Локальный кандидат меню
дополняет полужирное начертание 54 кириллическими глифами; исходные латинские
контуры, метрики и alignment zones сохранены по кодам. Новый шрифт использует
местные рецепты из исключённого из Git файла. Контуры и шрифтовые ресурсы
не публикуются. Визуальные отличия новых составных глифов, антиалиасинг,
переносы подсказок и доступные состояния нужно проверить в игре.

Сборка читает проверенные адреса из существующего workspace. Пример для уже
подготовленных **локальных** файлов; это не загрузка готовых переводов из Git:

```sh
python addressed_localization.py --source /path/to/original/gc-cs-steam.swf \
  --workspace local/extraction/translation-workspace.json \
  --plan local/extraction/options-plan.json \
  --font-recipes local/extraction/options-font-recipes.json \
  --output-dir output/options-candidate
# После проверки оригинальной установки; для обновления сначала restore:
python install_patch.py apply --game-file /path/to/game/gc-cs-steam.swf \
  --candidate output/options-candidate/gc-cs-steam.ru.swf \
  --workspace local/extraction/translation-workspace.json \
  --plan local/extraction/options-plan.json \
  --font-recipes local/extraction/options-font-recipes.json
python verify_experiment.py --game-file /path/to/game/gc-cs-steam.swf \
  --workspace local/extraction/translation-workspace.json \
  --plan local/extraction/options-plan.json \
  --font-recipes local/extraction/options-font-recipes.json
```

`--output-dir` должен быть новым: сборка не перезаписывает кандидаты и отчёты.
Installer и verifier повторяют сборку по локальным данным и сравнивают весь
кандидат. Без трёх дополнительных аргументов действует прежний рецепт заголовка.
Формат выбора адресов и ограничения — [в документации переводов](docs/translations.md).

Инструменты этапа 2 сохраняют полный структурный локальный снимок DefineText/2,
DefineEditText и строк/использований ABC. Поиск видимых состояний требует проверки
связей и runtime; [статистика и пробелы](docs/string-inventory.md).
HTML-переписывание, расширение на другие экраны, новые версии игры и универсальная
релокация ABC не реализованы. Новые переводы, оригиналы, рецепты и скриншоты
остаются локально; публикационный allowlist не расширен.

Подробнее: [формат и извлечение](docs/translations.md),
[адаптация шрифтов](docs/fonts.md), [аудит](docs/audit.md),
[правовой статус](docs/legal.md).
