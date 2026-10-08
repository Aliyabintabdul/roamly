import tempfile, unittest
from datetime import date, timedelta
from pathlib import Path
import harness
from harness import Client, Server

D = lambda n: (date.today() + timedelta(days=n)).isoformat()


class RoamlyTests(harness.AppTestCase):
    def new_trip(self, c, **kw):
        body = {'name': 'Lisbon', 'destination': 'Lisbon', 'start_date': D(10), 'end_date': D(12), 'currency': 'EUR', 'budget': 1000, **kw}
        return self.assertStatus(c.post('/api/trips', body), 201)['id']

    def test_trip_validation(self):
        c = self.client('v@example.com')
        e = self.assertStatus(c.post('/api/trips', {'name': '', 'destination': 'X', 'start_date': D(5), 'end_date': D(1), 'currency': 'XXX'}), 400)
        self.assertTrue({'name', 'end_date', 'currency'} <= set(e['fields']))
        self.assertStatus(c.post('/api/trips', {'name': 'Long', 'destination': 'X', 'start_date': D(0), 'end_date': D(200)}), 400)

    def test_trip_activity_expense_relationships(self):
        c = self.client('rel@example.com')
        tid = self.new_trip(c)
        # activities must fall within the trip and have a sensible time range
        self.assertStatus(c.post(f'/api/trips/{tid}/activities', {'title': 'Too early', 'day': D(9)}), 400)
        self.assertStatus(c.post(f'/api/trips/{tid}/activities', {'title': 'Backwards', 'day': D(10), 'start_time': '12:00', 'end_time': '11:00'}), 400)
        a1 = self.assertStatus(c.post(f'/api/trips/{tid}/activities', {'title': 'Tram 28', 'day': D(11), 'start_time': '09:00', 'est_cost': 30, 'booking_status': 'Idea'}), 201)['id']
        self.assertStatus(c.post(f'/api/trips/{tid}/activities', {'title': 'Fado', 'day': D(10), 'start_time': '20:00', 'est_cost': 70}), 201)
        self.assertStatus(c.post(f'/api/trips/{tid}/expenses', {'description': 'Tram', 'amount': 0, 'spent_on': D(11)}), 400)
        self.assertStatus(c.post(f'/api/trips/{tid}/expenses', {'description': 'Tram', 'amount': 32.5, 'spent_on': D(11), 'category': 'Transport', 'activity_id': a1}), 201)
        t = self.assertStatus(c.get(f'/api/trips/{tid}'), 200)
        self.assertEqual([a['title'] for a in t['activities']], ['Fado', 'Tram 28'])  # day-by-day order
        self.assertEqual((t['budget'], t['estimated'], t['spent']), (1000, 100, 32.5))  # estimated vs actual kept separate
        self.assertEqual(t['expenses'][0]['activity_title'], 'Tram 28')
        # currency is locked once amounts exist; dates cannot orphan activities
        self.assertStatus(c.put(f'/api/trips/{tid}', {'name': 'Lisbon', 'destination': 'Lisbon', 'start_date': D(10), 'end_date': D(12), 'currency': 'USD'}), 400)
        self.assertStatus(c.put(f'/api/trips/{tid}', {'name': 'Lisbon', 'destination': 'Lisbon', 'start_date': D(11), 'end_date': D(12), 'currency': 'EUR'}), 400)
        self.assertStatus(c.put(f'/api/trips/{tid}', {'name': 'Lisbon!', 'destination': 'Lisbon', 'start_date': D(10), 'end_date': D(13), 'currency': 'EUR'}), 200)
        # an expense can only link to an activity on the same trip
        other = self.new_trip(c, name='Porto')
        self.assertStatus(c.post(f'/api/trips/{other}/expenses', {'description': 'x', 'amount': 5, 'spent_on': D(1), 'activity_id': a1}), 400)
        # deleting an activity keeps the expense but unlinks it
        self.assertStatus(c.delete(f'/api/activities/{a1}'), 200)
        t = c.get(f'/api/trips/{tid}')[1]
        self.assertEqual((len(t['expenses']), t['expenses'][0]['activity_id']), (1, None))
        self.assertEqual(len(c.get('/api/trips?when=upcoming')[1]), 2)
        self.assertEqual(len(c.get('/api/trips?when=past')[1]), 0)
        self.assertStatus(c.delete(f'/api/trips/{tid}'), 200)
        self.assertStatus(c.get(f'/api/trips/{tid}'), 404)

    def test_account_isolation(self):
        a, b = self.client('ra@example.com'), self.client('rb@example.com')
        tid = self.new_trip(a)
        aid = a.post(f'/api/trips/{tid}/activities', {'title': 'Private', 'day': D(10)})[1]['id']
        self.assertEqual(b.get('/api/trips')[1], [])
        self.assertStatus(b.get(f'/api/trips/{tid}'), 404)
        self.assertStatus(b.post(f'/api/trips/{tid}/activities', {'title': 'x', 'day': D(10)}), 404)
        self.assertStatus(b.post(f'/api/trips/{tid}/expenses', {'description': 'x', 'amount': 1, 'spent_on': D(0)}), 404)
        self.assertStatus(b.put(f'/api/activities/{aid}', {'title': 'x', 'day': D(10)}), 404)
        self.assertStatus(b.delete(f'/api/trips/{tid}'), 404)
        self.assertStatus(b.get(f'/api/export/trips/{tid}/itinerary', raw=True), 404)

    def test_csv_export(self):
        c = self.client('csv@example.com')
        tid = self.new_trip(c, name='-2+3')
        c.post(f'/api/trips/{tid}/activities', {'title': '=cmd|calc', 'day': D(10)})
        s, text = c.get(f'/api/export/trips/{tid}/itinerary', raw=True)
        self.assertEqual(s, 200); self.assertIn("'=cmd|calc", text)
        s, text = c.get('/api/export/trips', raw=True)
        self.assertIn("'-2+3", text)


class Persistence(unittest.TestCase):
    def test_restart_keeps_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv = Server(Path(tmp) / 'p.sqlite3').start()
            c = Client(srv.port); c.register('p@example.com')
            c.post('/api/trips', {'name': 'Kept', 'destination': 'Oslo', 'start_date': D(1), 'end_date': D(2), 'currency': 'EUR'})
            srv.stop(); srv.start()
            c2 = Client(srv.port); c2.login('p@example.com')
            self.assertEqual(c2.get('/api/trips')[1][0]['destination'], 'Oslo')
            srv.stop()


if __name__ == '__main__':
    unittest.main()
