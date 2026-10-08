"""Roamly — personal travel itinerary and budget planner (Python standard library + SQLite).

Scheduling: activity dates/times are stored exactly as entered and mean *local time at
the destination*; no time-zone conversion is performed.
Currency: each trip has one currency and every amount on that trip (budget, estimates,
expenses) is recorded in it. Roamly does not convert currencies.
"""
from datetime import date, timedelta
from pathlib import Path
from core import App, ApiError, Csv, Form, not_found, now_iso, one, rows, today

CURRENCIES = ['AED', 'USD', 'EUR', 'GBP', 'JPY', 'TRY', 'SAR', 'INR', 'THB', 'CAD', 'AUD', 'CHF']
BOOKING = ['Idea', 'Planned', 'Reserved', 'Booked & paid', 'No booking needed']
ACT_CATEGORIES = ['Sightseeing', 'Food & drink', 'Transport', 'Lodging', 'Tour', 'Culture', 'Nature', 'Shopping', 'Other']
EXP_CATEGORIES = ['Transport', 'Lodging', 'Food & drink', 'Activities', 'Shopping', 'Fees & insurance', 'Other']
MAX_TRIP_DAYS = 90

SCHEMA = """
CREATE TABLE trips(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, destination TEXT NOT NULL, country TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  currency TEXT NOT NULL, budget REAL CHECK(budget>=0), timezone TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
  created TEXT NOT NULL, updated TEXT NOT NULL, CHECK(end_date>=start_date));
CREATE INDEX trips_user ON trips(user_id, start_date);
CREATE TABLE activities(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE, day TEXT NOT NULL, start_time TEXT, end_time TEXT,
  title TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'Other', est_cost REAL NOT NULL DEFAULT 0 CHECK(est_cost>=0),
  booking_status TEXT NOT NULL DEFAULT 'Planned', booking_ref TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
CREATE INDEX activities_trip ON activities(trip_id, day, start_time);
CREATE TABLE expenses(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE, activity_id INTEGER REFERENCES activities(id) ON DELETE SET NULL,
  spent_on TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL CHECK(amount>0), notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
CREATE INDEX expenses_trip ON expenses(trip_id, spent_on)
"""


def migrate_v1(c):
    for stmt in SCHEMA.split(';'):
        c.execute(stmt)
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='records'").fetchone():
        booking = {'Idea': 'Idea', 'Planned': 'Planned', 'Booked': 'Booked & paid', 'Visited': 'Booked & paid'}
        recs = c.execute('SELECT * FROM records ORDER BY user_id, category, due').fetchall()
        trips = {}
        for r in recs:  # legacy rows: title=activity, category=trip name, amount=budget, due=date
            key = (r['user_id'], r['category'] or 'My trip')
            trips.setdefault(key, []).append(r)
        ts = now_iso()
        for (uid, name), items in trips.items():
            days = sorted(i['due'] for i in items)
            tid = c.execute("""INSERT INTO trips(user_id,name,destination,start_date,end_date,currency,notes,created,updated) VALUES(?,?,?,?,?,'AED',?,?,?)""",
                            (uid, name, name, days[0], days[-1], 'Migrated from earlier version.', ts, ts)).lastrowid
            for i in items:
                c.execute("""INSERT INTO activities(user_id,trip_id,day,title,est_cost,booking_status,notes,created) VALUES(?,?,?,?,?,?,?,?)""",
                          (uid, tid, i['due'], i['title'] or 'Activity', max(i['amount'] or 0, 0), booking.get(i['status'], 'Planned'), i['notes'] or '', ts))
        c.execute('ALTER TABLE records RENAME TO legacy_records')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone():
            c.execute('ALTER TABLE events RENAME TO legacy_events')


def seed(c, uid):
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()
    ts = now_iso()
    t1 = c.execute("""INSERT INTO trips(user_id,name,destination,country,start_date,end_date,currency,budget,timezone,notes,created,updated)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (uid, 'Istanbul long weekend', 'Istanbul', 'Türkiye', d(21), d(24), 'TRY', 30000, 'Europe/Istanbul (UTC+3)', 'Fictional demo trip.', ts, ts)).lastrowid
    t2 = c.execute("""INSERT INTO trips(user_id,name,destination,country,start_date,end_date,currency,budget,timezone,notes,created,updated)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (uid, 'Dubai weekend', 'Dubai', 'United Arab Emirates', d(-20), d(-18), 'AED', 2500, 'Asia/Dubai (UTC+4)', 'Fictional demo trip.', ts, ts)).lastrowid
    acts = [(t1, d(21), '14:00', '15:30', 'Check in and walk Galata', 'Galata, Beyoğlu', 'Lodging', 0, 'Booked & paid'),
            (t1, d(21), '19:30', None, 'Dinner by the Bosphorus', 'Karaköy', 'Food & drink', 1800, 'Reserved'),
            (t1, d(22), '09:00', '12:00', 'Old town walking tour', 'Sultanahmet', 'Tour', 1200, 'Booked & paid'),
            (t1, d(22), '15:00', '17:00', 'Grand Bazaar', 'Fatih', 'Shopping', 2000, 'No booking needed'),
            (t1, d(23), '10:00', '13:00', 'Bosphorus ferry', 'Eminönü pier', 'Transport', 300, 'Idea'),
            (t2, d(-20), '10:00', '12:00', 'Museum of the Future', 'Sheikh Zayed Road', 'Culture', 169, 'Booked & paid'),
            (t2, d(-19), '15:30', '21:00', 'Desert sunset safari', 'Al Lahbab', 'Tour', 250, 'Booked & paid')]
    ids = []
    for a in acts:
        ids.append(c.execute("""INSERT INTO activities(user_id,trip_id,day,start_time,end_time,title,location,category,est_cost,booking_status,created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (uid, *a, ts)).lastrowid)
    for tid, aid, when, cat, desc, amt in [(t1, None, d(-2), 'Lodging', 'Hotel deposit (3 nights)', 6000), (t1, ids[2], d(-1), 'Activities', 'Walking tour tickets', 1150),
                                           (t2, ids[5], d(-20), 'Activities', 'Museum tickets', 169), (t2, ids[6], d(-19), 'Activities', 'Safari', 275),
                                           (t2, None, d(-19), 'Food & drink', 'Meals', 410), (t2, None, d(-20), 'Transport', 'Metro and taxis', 95)]:
        c.execute('INSERT INTO expenses(user_id,trip_id,activity_id,spent_on,category,description,amount,created) VALUES(?,?,?,?,?,?,?,?)', (uid, tid, aid, when, cat, desc, amt, ts))


app = App('Roamly', 'roamly', Path(__file__).parent, [migrate_v1], seed)


def own_trip(ctx, tid):
    return one(ctx.db, 'SELECT * FROM trips WHERE id=? AND user_id=?', (tid, ctx.uid), 'Trip')


def trip_form(body):
    f = Form(body)
    f.text('name', True, 120); f.text('destination', True, 120); f.text('country', False, 80)
    f.date('start_date', True); f.date('end_date', True); f.choice('currency', CURRENCIES, 'AED')
    f.number('budget', False, 0, 1e10); f.text('timezone', False, 60); f.text('notes', False, 5000)
    v = f.values
    if v['start_date'] and v['end_date']:
        f.check(v['end_date'] >= v['start_date'], 'end_date', 'The trip must end on or after its start date.')
        f.check((date.fromisoformat(v['end_date']) - date.fromisoformat(v['start_date'])).days < MAX_TRIP_DAYS, 'end_date', f'Trips can be at most {MAX_TRIP_DAYS} days.')
    return f.done()


def activity_form(body, trip):
    f = Form(body)
    f.date('day', True); f.time('start_time'); f.time('end_time'); f.text('title', True, 160); f.text('location', False, 160)
    f.choice('category', ACT_CATEGORIES, 'Other'); f.number('est_cost', False, 0, 1e10, default=0)
    f.choice('booking_status', BOOKING, 'Planned'); f.text('booking_ref', False, 80); f.text('notes', False, 3000)
    v = f.values
    f.check(not v['day'] or trip['start_date'] <= v['day'] <= trip['end_date'], 'day', f'Choose a day between {trip["start_date"]} and {trip["end_date"]}.')
    f.check(not v['end_time'] or (v['start_time'] and v['end_time'] > v['start_time']), 'end_time', 'End time must be after the start time on the same day.')
    return f.done()


def expense_form(ctx, body, trip):
    f = Form(body)
    f.date('spent_on', True); f.choice('category', EXP_CATEGORIES, 'Other'); f.text('description', True, 160)
    f.number('amount', True, 0.01, 1e10); f.number('activity_id', False, 1, 1e12, integer=True); f.text('notes', False, 2000)
    v = f.done()
    if v['activity_id'] and not ctx.db.execute('SELECT 1 FROM activities WHERE id=? AND trip_id=? AND user_id=?', (v['activity_id'], trip['id'], ctx.uid)).fetchone():
        raise ApiError(400, 'Linked activity must belong to this trip.', 'validation_error', {'activity_id': 'Choose an activity from this trip.'})
    return v


TRIP_SELECT = """SELECT t.*, (SELECT COUNT(*) FROM activities a WHERE a.trip_id=t.id) AS activity_count,
  (SELECT COALESCE(SUM(est_cost),0) FROM activities a WHERE a.trip_id=t.id) AS estimated,
  (SELECT COALESCE(SUM(amount),0) FROM expenses e WHERE e.trip_id=t.id) AS spent,
  CASE WHEN t.end_date < :today THEN 'past' WHEN t.start_date > :today THEN 'upcoming' ELSE 'current' END AS phase
  FROM trips t WHERE t.user_id=:uid"""


def trip_query(ctx):
    sql, args = TRIP_SELECT, {'uid': ctx.uid, 'today': today()}
    if ctx.q('q'):
        sql += ' AND (t.name LIKE :q OR t.destination LIKE :q OR t.country LIKE :q)'; args['q'] = f"%{ctx.q('q')}%"
    when = ctx.q('when')
    if when == 'upcoming':
        sql += ' AND t.start_date > :today'
    elif when == 'current':
        sql += ' AND t.start_date <= :today AND t.end_date >= :today'
    elif when == 'past':
        sql += ' AND t.end_date < :today'
    return rows(ctx.db.execute(sql + (' ORDER BY t.start_date DESC' if when == 'past' else ' ORDER BY t.end_date < :today, t.start_date'), args))


@app.route('GET', '/api/meta')
def meta(ctx):
    return {'currencies': CURRENCIES, 'booking': BOOKING, 'activity_categories': ACT_CATEGORIES, 'expense_categories': EXP_CATEGORIES, 'max_trip_days': MAX_TRIP_DAYS}


@app.route('GET', '/api/dashboard')
def dashboard(ctx):
    trips = trip_query(ctx)
    totals = {}
    for t in trips:
        s = totals.setdefault(t['currency'], {'currency': t['currency'], 'budget': 0, 'estimated': 0, 'spent': 0, 'trips': 0})
        s['budget'] += t['budget'] or 0; s['estimated'] += t['estimated']; s['spent'] += t['spent']; s['trips'] += 1
    nxt = next((t for t in trips if t['phase'] != 'past'), None)
    plan = []
    if nxt:
        plan = rows(ctx.db.execute('SELECT * FROM activities WHERE trip_id=? ORDER BY day, start_time IS NULL, start_time LIMIT 5', (nxt['id'],)))
    unbooked = ctx.db.execute("""SELECT COUNT(*) FROM activities a JOIN trips t ON t.id=a.trip_id WHERE a.user_id=? AND t.end_date>=?
        AND a.booking_status IN ('Idea','Planned')""", (ctx.uid, today())).fetchone()[0]
    return {'trips': trips, 'totals': sorted(totals.values(), key=lambda x: x['currency']), 'next': nxt, 'next_plan': plan, 'unbooked': unbooked}


@app.route('GET', '/api/trips')
def list_trips(ctx):
    return trip_query(ctx)


@app.route('POST', '/api/trips')
def create_trip(ctx):
    v, ts = trip_form(ctx.body), now_iso()
    tid = ctx.db.execute("""INSERT INTO trips(user_id,name,destination,country,start_date,end_date,currency,budget,timezone,notes,created,updated)
        VALUES(:uid,:name,:destination,:country,:start_date,:end_date,:currency,:budget,:timezone,:notes,:ts,:ts)""", {**v, 'uid': ctx.uid, 'ts': ts}).lastrowid
    return 201, {'id': tid}


@app.route('GET', '/api/trips/{id}')
def get_trip(ctx):
    ctx.query = {}
    t = next((x for x in trip_query(ctx) if x['id'] == ctx.params['id']), None)
    if not t:
        raise not_found('Trip')
    t['activities'] = rows(ctx.db.execute('SELECT * FROM activities WHERE trip_id=? AND user_id=? ORDER BY day, start_time IS NULL, start_time, id', (t['id'], ctx.uid)))
    t['expenses'] = rows(ctx.db.execute("""SELECT e.*, a.title AS activity_title FROM expenses e LEFT JOIN activities a ON a.id=e.activity_id
        WHERE e.trip_id=? AND e.user_id=? ORDER BY e.spent_on DESC, e.id DESC""", (t['id'], ctx.uid)))
    t['spent_by_category'] = rows(ctx.db.execute('SELECT category, SUM(amount) total FROM expenses WHERE trip_id=? GROUP BY category ORDER BY total DESC', (t['id'],)))
    t['estimated_by_booking'] = rows(ctx.db.execute('SELECT booking_status, COUNT(*) n, SUM(est_cost) total FROM activities WHERE trip_id=? GROUP BY booking_status', (t['id'],)))
    return t


@app.route('PUT', '/api/trips/{id}')
def update_trip(ctx):
    old = own_trip(ctx, ctx.params['id'])
    v = trip_form(ctx.body)
    outside = ctx.db.execute('SELECT COUNT(*) FROM activities WHERE trip_id=? AND (day<? OR day>?)', (old['id'], v['start_date'], v['end_date'])).fetchone()[0]
    if outside:
        raise ApiError(400, f'{outside} planned activit{"y falls" if outside == 1 else "ies fall"} outside the new dates. Move or delete them first.',
                       'domain_rule', {'start_date': 'Activities exist outside this range.'})
    if v['currency'] != old['currency'] and ctx.db.execute('SELECT 1 FROM expenses WHERE trip_id=? UNION SELECT 1 FROM activities WHERE trip_id=? AND est_cost>0', (old['id'], old['id'])).fetchone():
        raise ApiError(400, 'This trip already has amounts in ' + old['currency'] + '. Roamly does not convert currencies, so the currency cannot be changed.',
                       'domain_rule', {'currency': 'Locked: amounts already recorded.'})
    ctx.db.execute("""UPDATE trips SET name=:name,destination=:destination,country=:country,start_date=:start_date,end_date=:end_date,currency=:currency,
        budget=:budget,timezone=:timezone,notes=:notes,updated=:ts WHERE id=:id AND user_id=:uid""", {**v, 'ts': now_iso(), 'id': old['id'], 'uid': ctx.uid})
    return {'ok': True}


@app.route('DELETE', '/api/trips/{id}')
def delete_trip(ctx):
    own_trip(ctx, ctx.params['id'])
    ctx.db.execute('DELETE FROM trips WHERE id=? AND user_id=?', (ctx.params['id'], ctx.uid))
    return {'ok': True}


@app.route('POST', '/api/trips/{id}/activities')
def create_activity(ctx):
    trip = own_trip(ctx, ctx.params['id'])
    v = activity_form(ctx.body, trip)
    aid = ctx.db.execute("""INSERT INTO activities(user_id,trip_id,day,start_time,end_time,title,location,category,est_cost,booking_status,booking_ref,notes,created)
        VALUES(:uid,:tid,:day,:start_time,:end_time,:title,:location,:category,:est_cost,:booking_status,:booking_ref,:notes,:ts)""", {**v, 'uid': ctx.uid, 'tid': trip['id'], 'ts': now_iso()}).lastrowid
    return 201, {'id': aid}


@app.route('PUT', '/api/activities/{id}')
def update_activity(ctx):
    act = one(ctx.db, 'SELECT * FROM activities WHERE id=? AND user_id=?', (ctx.params['id'], ctx.uid), 'Activity')
    v = activity_form(ctx.body, own_trip(ctx, act['trip_id']))
    ctx.db.execute("""UPDATE activities SET day=:day,start_time=:start_time,end_time=:end_time,title=:title,location=:location,category=:category,est_cost=:est_cost,
        booking_status=:booking_status,booking_ref=:booking_ref,notes=:notes WHERE id=:id AND user_id=:uid""", {**v, 'id': act['id'], 'uid': ctx.uid})
    return {'ok': True}


@app.route('DELETE', '/api/activities/{id}')
def delete_activity(ctx):
    one(ctx.db, 'SELECT id FROM activities WHERE id=? AND user_id=?', (ctx.params['id'], ctx.uid), 'Activity')
    ctx.db.execute('DELETE FROM activities WHERE id=? AND user_id=?', (ctx.params['id'], ctx.uid))
    return {'ok': True}


@app.route('POST', '/api/trips/{id}/expenses')
def create_expense(ctx):
    trip = own_trip(ctx, ctx.params['id'])
    v = expense_form(ctx, ctx.body, trip)
    eid = ctx.db.execute("""INSERT INTO expenses(user_id,trip_id,activity_id,spent_on,category,description,amount,notes,created)
        VALUES(:uid,:tid,:activity_id,:spent_on,:category,:description,:amount,:notes,:ts)""", {**v, 'uid': ctx.uid, 'tid': trip['id'], 'ts': now_iso()}).lastrowid
    return 201, {'id': eid}


@app.route('PUT', '/api/expenses/{id}')
def update_expense(ctx):
    exp = one(ctx.db, 'SELECT * FROM expenses WHERE id=? AND user_id=?', (ctx.params['id'], ctx.uid), 'Expense')
    v = expense_form(ctx, ctx.body, own_trip(ctx, exp['trip_id']))
    ctx.db.execute("""UPDATE expenses SET activity_id=:activity_id,spent_on=:spent_on,category=:category,description=:description,amount=:amount,notes=:notes
        WHERE id=:id AND user_id=:uid""", {**v, 'id': exp['id'], 'uid': ctx.uid})
    return {'ok': True}


@app.route('DELETE', '/api/expenses/{id}')
def delete_expense(ctx):
    one(ctx.db, 'SELECT id FROM expenses WHERE id=? AND user_id=?', (ctx.params['id'], ctx.uid), 'Expense')
    ctx.db.execute('DELETE FROM expenses WHERE id=? AND user_id=?', (ctx.params['id'], ctx.uid))
    return {'ok': True}


@app.route('GET', '/api/export/trips')
def export_trips(ctx):
    data = [[t['name'], t['destination'], t['country'], t['start_date'], t['end_date'], t['currency'], t['budget'], t['estimated'], t['spent'], t['activity_count'], t['phase']]
            for t in trip_query(ctx)]
    return Csv(f'roamly-trips-{today()}.csv', ['Trip', 'Destination', 'Country', 'Start', 'End', 'Currency', 'Planned budget', 'Estimated activity cost', 'Actual spent', 'Activities', 'Phase'], data)


@app.route('GET', '/api/export/trips/{id}/itinerary')
def export_itinerary(ctx):
    t = own_trip(ctx, ctx.params['id'])
    acts = ctx.db.execute('SELECT * FROM activities WHERE trip_id=? ORDER BY day, start_time IS NULL, start_time', (t['id'],))
    exps = ctx.db.execute('SELECT * FROM expenses WHERE trip_id=? ORDER BY spent_on', (t['id'],))
    data = [['Activity', a['day'], a['start_time'], a['end_time'], a['title'], a['location'], a['category'], a['booking_status'], a['est_cost'], t['currency']] for a in acts]
    data += [['Expense', e['spent_on'], '', '', e['description'], '', e['category'], '', e['amount'], t['currency']] for e in exps]
    return Csv(f'roamly-trip-{t["id"]}-{today()}.csv', ['Type', 'Date', 'Start (local)', 'End (local)', 'Title', 'Location', 'Category', 'Booking status', 'Amount (est. / actual)', 'Currency'], data)


if __name__ == '__main__':
    app.main(8003)
