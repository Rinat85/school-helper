-- school-helper — полная схема БД (SQLite)
--
-- Соглашения:
--   * все метки времени  — TEXT в ISO-8601 UTC ('2026-09-18T14:30:00Z');
--   * все даты           — TEXT 'YYYY-MM-DD' (локальная дата класса);
--   * все денежные суммы — INTEGER в сумах, целые. Никаких float;
--   * class_id есть везде — мультиклассовость заложена с первого дня;
--   * булевы значения    — INTEGER 0/1.

PRAGMA foreign_keys = ON;

-- ────────────────────────────────────────────────────────────── ЯДРО ──

CREATE TABLE IF NOT EXISTS klass (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,                       -- '1 «В»'
    school      TEXT,                                   -- 'Школа №101'
    tg_chat_id  INTEGER UNIQUE,                         -- группа родителей
    timezone    TEXT    NOT NULL DEFAULT 'Asia/Tashkent',
    currency    TEXT    NOT NULL DEFAULT 'UZS',
    card_number TEXT,                                   -- реквизиты для сборов
    card_holder TEXT,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS person (
    id           INTEGER PRIMARY KEY,
    class_id     INTEGER NOT NULL REFERENCES klass(id),
    tg_user_id   INTEGER,                               -- NULL = оффлайн-родитель, ведёт казначей
    tg_username  TEXT,
    display_name TEXT    NOT NULL,                      -- 'Анна И.'
    child_name   TEXT,                                  -- ТОЛЬКО имя, без фамилии
    status       TEXT    NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'left')),
    dm_open      INTEGER NOT NULL DEFAULT 0,            -- нажал ли /start в личке
    joined_at    TEXT    NOT NULL,
    left_at      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_person_tg
    ON person(class_id, tg_user_id) WHERE tg_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_person_class ON person(class_id, status);

CREATE TABLE IF NOT EXISTS person_role (
    id         INTEGER PRIMARY KEY,
    person_id  INTEGER NOT NULL REFERENCES person(id),
    role       TEXT    NOT NULL
               CHECK (role IN ('parent','treasurer','chair','auditor','teacher','admin')),
    granted_by INTEGER REFERENCES person(id),
    granted_at TEXT    NOT NULL,
    revoked_by INTEGER REFERENCES person(id),
    revoked_at TEXT                                     -- NULL = роль действует
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_role_active
    ON person_role(person_id, role) WHERE revoked_at IS NULL;

-- ─────────────────────────────────────────────────────────── СОБЫТИЯ ──
-- event_case — длящееся дело со стадиями. Ядро всей системы:
-- голосования, сборы, расходы и даты привязываются к нему.

CREATE TABLE IF NOT EXISTS event_case (
    id            INTEGER PRIMARY KEY,
    class_id      INTEGER NOT NULL REFERENCES klass(id),
    title         TEXT    NOT NULL,                     -- 'Поездка в цирк'
    kind          TEXT    NOT NULL DEFAULT 'other'
                  CHECK (kind IN ('trip','gift','purchase','repair','holiday','other')),
    status        TEXT    NOT NULL DEFAULT 'idea'
                  CHECK (status IN ('idea','voting','collecting','executing','done','cancelled')),
    owner_id      INTEGER REFERENCES person(id),
    summary       TEXT,
    outcome       TEXT,                                 -- итог, пишет председатель при закрытии
    cover_file_id INTEGER REFERENCES file(id),
    opened_at     TEXT    NOT NULL,
    closed_at     TEXT
);

CREATE INDEX IF NOT EXISTS ix_case_class ON event_case(class_id, status, opened_at);
CREATE INDEX IF NOT EXISTS ix_case_closed ON event_case(class_id, closed_at);

-- Хроника. Пишется АВТОМАТИЧЕСКИ из всех модулей, меняющих состояние.
CREATE TABLE IF NOT EXISTS case_entry (
    id         INTEGER PRIMARY KEY,
    case_id    INTEGER NOT NULL REFERENCES event_case(id),
    at         TEXT    NOT NULL,
    type       TEXT    NOT NULL,                        -- opened|poll_opened|poll_closed|
                                                        -- collection_opened|payment_confirmed|
                                                        -- collection_closed|expense_added|
                                                        -- expense_approved|note|photo|
                                                        -- calendar_added|closed
    actor_id   INTEGER REFERENCES person(id),
    ref_type   TEXT,                                    -- poll|collection|expense|calendar_event|file
    ref_id     INTEGER,
    text       TEXT,                                    -- человекочитаемая строка для ленты
    visibility TEXT    NOT NULL DEFAULT 'all'
               CHECK (visibility IN ('all','money_roles'))
);

CREATE INDEX IF NOT EXISTS ix_case_entry ON case_entry(case_id, at);

-- ───────────────────────────────────────────────────────────── ДЕНЬГИ ──

CREATE TABLE IF NOT EXISTS collection (
    id                INTEGER PRIMARY KEY,
    class_id          INTEGER NOT NULL REFERENCES klass(id),
    case_id           INTEGER REFERENCES event_case(id),
    title             TEXT    NOT NULL,
    purpose           TEXT,
    amount_per_person INTEGER,
    total_target      INTEGER,
    due_date          TEXT,
    voluntary         INTEGER NOT NULL DEFAULT 1,       -- всегда 1, см. SPEC §2
    payment_code      TEXT,                             -- '1V-NG' в комментарии перевода
    status            TEXT    NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','open','closed','cancelled')),
    tg_message_id     INTEGER,                          -- закреплённое сообщение с прогрессом
    created_by        INTEGER REFERENCES person(id),
    created_at        TEXT    NOT NULL,
    closed_at         TEXT
);

CREATE INDEX IF NOT EXISTS ix_collection_class ON collection(class_id, status);

-- Ожидание с конкретного родителя.
CREATE TABLE IF NOT EXISTS contribution (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collection(id),
    person_id     INTEGER NOT NULL REFERENCES person(id),
    expected      INTEGER NOT NULL,
    waived        INTEGER NOT NULL DEFAULT 0,           -- освобождён, тихо, без статуса «должник»
    note          TEXT,
    UNIQUE (collection_id, person_id)
);

CREATE TABLE IF NOT EXISTS payment (
    id              INTEGER PRIMARY KEY,
    contribution_id INTEGER REFERENCES contribution(id),
    person_id       INTEGER NOT NULL REFERENCES person(id),
    amount          INTEGER NOT NULL CHECK (amount > 0),
    method          TEXT    NOT NULL DEFAULT 'transfer'
                    CHECK (method IN ('transfer','cash')),
    receipt_id      INTEGER REFERENCES file(id),
    status          TEXT    NOT NULL DEFAULT 'claimed'
                    CHECK (status IN ('claimed','confirmed','rejected')),
    claimed_at      TEXT,
    confirmed_by    INTEGER REFERENCES person(id),
    confirmed_at    TEXT,
    reject_reason   TEXT,
    entered_by      INTEGER REFERENCES person(id),      -- казначей внёс за оффлайн-родителя
    idempotency_key TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_idem
    ON payment(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_payment_status ON payment(status, claimed_at);
CREATE INDEX IF NOT EXISTS ix_payment_contrib ON payment(contribution_id);

CREATE TABLE IF NOT EXISTS expense (
    id          INTEGER PRIMARY KEY,
    class_id    INTEGER NOT NULL REFERENCES klass(id),
    case_id     INTEGER REFERENCES event_case(id),
    title       TEXT    NOT NULL,
    amount      INTEGER NOT NULL CHECK (amount > 0),
    spent_at    TEXT    NOT NULL,
    paid_by     INTEGER REFERENCES person(id),
    receipt_id  INTEGER REFERENCES file(id),
    status      TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected')),
    created_by  INTEGER REFERENCES person(id),
    approved_by INTEGER REFERENCES person(id),          -- правило двух рук: не равен created_by
    approved_at TEXT,
    note        TEXT,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_expense_class ON expense(class_id, status, spent_at);

-- Неизменяемый журнал. Правок нет — есть сторно.
-- Остаток = SUM(in) - SUM(out) по строкам, у которых нет сторно.
CREATE TABLE IF NOT EXISTS ledger_entry (
    id          INTEGER PRIMARY KEY,
    class_id    INTEGER NOT NULL REFERENCES klass(id),
    case_id     INTEGER REFERENCES event_case(id),
    occurred_at TEXT    NOT NULL,
    direction   TEXT    NOT NULL CHECK (direction IN ('in','out')),
    amount      INTEGER NOT NULL CHECK (amount > 0),    -- всегда положительное
    source_type TEXT    NOT NULL
                CHECK (source_type IN ('payment','expense','adjustment','carryover')),
    source_id   INTEGER,
    memo        TEXT,
    created_by  INTEGER REFERENCES person(id),
    created_at  TEXT    NOT NULL,
    reverses_id INTEGER REFERENCES ledger_entry(id)     -- сторно
);

CREATE INDEX IF NOT EXISTS ix_ledger_class ON ledger_entry(class_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_ledger_source ON ledger_entry(source_type, source_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ledger_reverses
    ON ledger_entry(reverses_id) WHERE reverses_id IS NOT NULL;

-- Поступления из банка, ещё не привязанные к взносу (экран сверки).
CREATE TABLE IF NOT EXISTS bank_inflow (
    id          INTEGER PRIMARY KEY,
    class_id    INTEGER NOT NULL REFERENCES klass(id),
    amount      INTEGER NOT NULL,
    occurred_at TEXT    NOT NULL,
    raw_text    TEXT,                                   -- исходная SMS/пуш
    code_hint   TEXT,                                   -- распознанный payment_code
    sender_hint TEXT,
    matched_payment_id INTEGER REFERENCES payment(id),
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_inflow_open
    ON bank_inflow(class_id, occurred_at) WHERE matched_payment_id IS NULL;

-- ──────────────────────────────────────────────────────── ГОЛОСОВАНИЯ ──

CREATE TABLE IF NOT EXISTS poll (
    id            INTEGER PRIMARY KEY,
    class_id      INTEGER NOT NULL REFERENCES klass(id),
    case_id       INTEGER REFERENCES event_case(id),
    question      TEXT    NOT NULL,
    description   TEXT,
    kind          TEXT    NOT NULL DEFAULT 'single'
                  CHECK (kind IN ('single','multi','date')),
    anonymous     INTEGER NOT NULL DEFAULT 0,
    quorum        INTEGER,
    deadline_at   TEXT,
    status        TEXT    NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','closed','cancelled')),
    result        TEXT,                                 -- JSON, фиксируется при закрытии
    tg_message_id INTEGER,                              -- сообщение в группе, редактируется
    created_by    INTEGER REFERENCES person(id),
    created_at    TEXT    NOT NULL,
    closed_at     TEXT
);

CREATE INDEX IF NOT EXISTS ix_poll_open ON poll(class_id, status, deadline_at);

CREATE TABLE IF NOT EXISTS poll_option (
    id      INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES poll(id),
    text    TEXT    NOT NULL,
    payload TEXT,                                       -- JSON: сумма, дата и т.п.
    sort    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_option_poll ON poll_option(poll_id, sort);

CREATE TABLE IF NOT EXISTS vote (
    id         INTEGER PRIMARY KEY,
    poll_id    INTEGER NOT NULL REFERENCES poll(id),
    option_id  INTEGER NOT NULL REFERENCES poll_option(id),
    person_id  INTEGER REFERENCES person(id),           -- NULL у анонимных
    voter_hash TEXT    NOT NULL,                        -- HMAC(secret, poll_id||person_id)
    comment    TEXT,
    created_at TEXT    NOT NULL,
    UNIQUE (poll_id, voter_hash, option_id)
);

CREATE INDEX IF NOT EXISTS ix_vote_poll ON vote(poll_id);

-- ────────────────────────────────────────────────────────── РАСПИСАНИЕ ──

CREATE TABLE IF NOT EXISTS timetable_version (
    id           INTEGER PRIMARY KEY,
    class_id     INTEGER NOT NULL REFERENCES klass(id),
    title        TEXT,                                  -- 'II четверть'
    valid_from   TEXT    NOT NULL,
    valid_to     TEXT,
    published_at TEXT,
    created_by   INTEGER REFERENCES person(id),
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tt_version ON timetable_version(class_id, valid_from);

CREATE TABLE IF NOT EXISTS lesson_slot (
    id         INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES timetable_version(id),
    weekday    INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),  -- 1=Пн
    slot_no    INTEGER NOT NULL,
    subject    TEXT    NOT NULL,
    teacher    TEXT,
    room       TEXT,
    starts_at  TEXT,                                    -- '08:30'
    ends_at    TEXT,
    UNIQUE (version_id, weekday, slot_no)
);

-- Замены на конкретный день — самая используемая часть расписания.
CREATE TABLE IF NOT EXISTS timetable_override (
    id         INTEGER PRIMARY KEY,
    class_id   INTEGER NOT NULL REFERENCES klass(id),
    date       TEXT    NOT NULL,
    slot_no    INTEGER,                                 -- NULL для shift_all
    action     TEXT    NOT NULL
               CHECK (action IN ('cancel','replace','add','shift_all')),
    subject    TEXT,
    room       TEXT,
    note       TEXT,                                    -- 'учитель болеет'
    announced  INTEGER NOT NULL DEFAULT 0,              -- отправлено ли в группу
    created_by INTEGER REFERENCES person(id),
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tt_override ON timetable_override(class_id, date);

-- ──────────────────────────────────────────────────────── ОБЪЯВЛЕНИЯ ──

CREATE TABLE IF NOT EXISTS announcement (
    id            INTEGER PRIMARY KEY,
    class_id      INTEGER NOT NULL REFERENCES klass(id),
    author_id     INTEGER NOT NULL REFERENCES person(id),
    on_behalf_of  INTEGER REFERENCES person(id),        -- «со слов учителя»
    title         TEXT,
    body          TEXT    NOT NULL,
    importance    TEXT    NOT NULL DEFAULT 'normal'
                  CHECK (importance IN ('normal','important','urgent')),
    require_ack   INTEGER NOT NULL DEFAULT 0,
    pinned_until  TEXT,
    tg_message_id INTEGER,
    published_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_ann_class ON announcement(class_id, published_at);

CREATE TABLE IF NOT EXISTS announcement_file (
    announcement_id INTEGER NOT NULL REFERENCES announcement(id),
    file_id         INTEGER NOT NULL REFERENCES file(id),
    PRIMARY KEY (announcement_id, file_id)
);

CREATE TABLE IF NOT EXISTS announcement_ack (
    announcement_id INTEGER NOT NULL REFERENCES announcement(id),
    person_id       INTEGER NOT NULL REFERENCES person(id),
    acked_at        TEXT    NOT NULL,
    PRIMARY KEY (announcement_id, person_id)
);

-- ────────────────────────────────────────────────────────── КАЛЕНДАРЬ ──

CREATE TABLE IF NOT EXISTS calendar_event (
    id            INTEGER PRIMARY KEY,
    class_id      INTEGER NOT NULL REFERENCES klass(id),
    case_id       INTEGER REFERENCES event_case(id),
    title         TEXT    NOT NULL,
    kind          TEXT    NOT NULL DEFAULT 'other'
                  CHECK (kind IN ('meeting','bring','trip','holiday','short_day','deadline','other')),
    starts_at     TEXT    NOT NULL,
    ends_at       TEXT,
    all_day       INTEGER NOT NULL DEFAULT 0,
    place         TEXT,
    description   TEXT,
    remind_before TEXT,                                 -- JSON: [1440, 120] минут до
    created_by    INTEGER REFERENCES person(id),
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_calendar ON calendar_event(class_id, starts_at);

-- ──────────────────────────────────────── ФАЙЛЫ, УВЕДОМЛЕНИЯ, АУДИТ ──

-- Файлы храним в Telegram по file_id: бесплатно, без S3.
-- Ограничение: file_id живёт, пока жив бот (смена токена → ссылки теряются).
CREATE TABLE IF NOT EXISTS file (
    id          INTEGER PRIMARY KEY,
    class_id    INTEGER NOT NULL REFERENCES klass(id),
    tg_file_id  TEXT    NOT NULL,
    tg_unique_id TEXT,
    kind        TEXT    NOT NULL DEFAULT 'photo'
                CHECK (kind IN ('receipt','photo','doc')),
    mime        TEXT,
    size        INTEGER,
    uploaded_by INTEGER REFERENCES person(id),
    uploaded_at TEXT    NOT NULL
);

-- Отправка только через очередь: лимиты Telegram (429), заблокировавшие бота, ретраи.
CREATE TABLE IF NOT EXISTS notification (
    id            INTEGER PRIMARY KEY,
    person_id     INTEGER NOT NULL REFERENCES person(id),
    kind          TEXT    NOT NULL,
    payload       TEXT,                                 -- JSON
    dedup_key     TEXT,                                 -- антиспам: одно напоминание на повод
    scheduled_at  TEXT    NOT NULL,
    sent_at       TEXT,
    tg_message_id INTEGER,
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','sent','failed','skipped')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_notif_dedup
    ON notification(dedup_key) WHERE dedup_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_notif_queue
    ON notification(status, scheduled_at) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    class_id    INTEGER NOT NULL REFERENCES klass(id),
    at          TEXT    NOT NULL,
    actor_id    INTEGER REFERENCES person(id),
    action      TEXT    NOT NULL,                       -- 'expense.approve', 'role.grant', ...
    object_type TEXT,
    object_id   INTEGER,
    before      TEXT,                                   -- JSON
    after       TEXT
);

CREATE INDEX IF NOT EXISTS ix_audit ON audit_log(class_id, at);

-- ─────────────────────────────────────────────────────────── СЛУЖЕБНОЕ ──

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
