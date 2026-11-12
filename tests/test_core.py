import os,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
class BankingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();app.DB=Path(self.tmp.name)/'test.sqlite3';app.init_db()
        self.alex=app.register('alex','TestPass123!');self.maya=app.register('maya','TestPass123!')
        with app.conn() as c:
            c.execute('INSERT INTO transactions(kind,reference,description) VALUES(?,?,?)',('seed','TEST-SEED','test only'))
            aid=app.account(c,self.alex)['id'];c.execute('INSERT INTO entries(transaction_id,account_id,amount) VALUES(?,?,?)',(1,aid,100000))
            # test-only treasury balancing counterpart
            treasury=app.account(c,self.maya)['id'];c.execute('INSERT INTO entries(transaction_id,account_id,amount) VALUES(?,?,?)',(1,treasury,-100000))
    def tearDown(self):self.tmp.cleanup()
    def test_balanced_transfer(self):
        ref,duplicate=app.transfer(self.alex,'FV00000002','125.50','one');self.assertFalse(duplicate)
        with app.conn() as c:
            self.assertEqual(app.balance(c,app.account(c,self.alex)['id']),87450)
            self.assertEqual(app.balance(c,app.account(c,self.maya)['id']),-87450)
            self.assertEqual(c.execute('SELECT SUM(amount) FROM entries WHERE transaction_id=(SELECT id FROM transactions WHERE reference=?)',(ref,)).fetchone()[0],0)
    def test_idempotency(self):
        x=app.transfer(self.alex,'FV00000002','10','repeat');y=app.transfer(self.alex,'FV00000002','10','repeat');self.assertEqual(x[0],y[0]);self.assertTrue(y[1])
    def test_insufficient_funds(self):
        with self.assertRaisesRegex(ValueError,'Insufficient'):app.transfer(self.alex,'FV00000002','2000','bad')
    def test_frozen_account(self):
        with app.conn() as c:c.execute('UPDATE accounts SET status="frozen" WHERE user_id=?',(self.maya,))
        with self.assertRaisesRegex(ValueError,'frozen'):app.transfer(self.alex,'FV00000002','10','bad')
    def test_invalid_amount(self):
        for v in ['-1','0','0.001','NaN','hello']:
            with self.subTest(v=v),self.assertRaises(ValueError):app.transfer(self.alex,'FV00000002',v,'invalid')
    def test_passwords(self):
        with app.conn() as c:p=c.execute('SELECT password FROM users WHERE id=?',(self.alex,)).fetchone()[0]
        self.assertTrue(app.password_ok('TestPass123!',p));self.assertFalse(app.password_ok('wrong',p));self.assertNotIn('TestPass123!',p)
    def test_duplicate_username(self):
        with self.assertRaises(Exception):app.register('alex','TestPass123!')
if __name__=='__main__':unittest.main()
