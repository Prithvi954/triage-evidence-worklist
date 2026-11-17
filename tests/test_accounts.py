"""Accounts: password hashing, login, throttling, idle expiry, roles, user administration."""
import time
import unittest

from app import accounts
from app.services import BadInput, Conflict, Forbidden, NotFound
from tests.helpers import make_app, user


class TestPasswords(unittest.TestCase):
    def test_hash_and_verify(self):
        h = accounts.hash_password("correct horse 42")
        self.assertTrue(h.startswith("pbkdf2_sha256$"))
        self.assertTrue(accounts.verify_password("correct horse 42", h))
        self.assertFalse(accounts.verify_password("wrong", h))
        self.assertNotEqual(h, accounts.hash_password("correct horse 42"))   # salted

    def test_password_rules(self):
        self.assertTrue(accounts.password_problems("short1"))
        self.assertTrue(accounts.password_problems("onlyletterslong"))
        self.assertEqual(accounts.password_problems("letters123456"), [])


class TestLogin(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.svc
        self.svc.throttle = accounts.Throttle()

    def test_username_password_login(self):
        tok, u = self.svc.login_password("riley", "demo")
        self.assertEqual(u["id"], "u_riley")
        self.assertEqual(self.svc.user_for_token(tok)["id"], "u_riley")
        with self.assertRaises(Forbidden):
            self.svc.login_password("riley", "nope")

    def test_throttle_after_five_failures(self):
        for _ in range(5):
            with self.assertRaises(Forbidden):
                self.svc.login_password("sam", "bad")
        with self.assertRaises(Forbidden) as ctx:
            self.svc.login_password("sam", "demo")         # right password, still locked
        self.assertIn("Too many", str(ctx.exception))

    def test_idle_session_expires(self):
        tok, _ = self.svc.login_password("riley", "demo")
        with self.app.store.tx() as c:
            c.execute("UPDATE sessions SET last_seen=? WHERE token=?", (time.time() - 31 * 60, tok))
        self.assertIsNone(self.svc.user_for_token(tok))

    def test_roles_land_on_their_home_and_scope(self):
        from app.server import _home_for
        self.assertEqual(_home_for("rn"), "/")
        self.assertEqual(_home_for("student"), "/learn.html")
        self.assertEqual(_home_for("admin"), "/admin.html")
        with self.assertRaises(Forbidden):
            self.svc.state(user(self.app, "u_taylor"))       # students never see the real board
        st = self.svc.state(user(self.app, "u_drlee"))
        self.assertTrue(st["me"]["can_confirm_priority"])   # residents can confirm priority

    def test_change_password(self):
        riley = user(self.app, "u_riley")
        with self.assertRaises(BadInput):
            self.svc.change_password(riley, "demo", "short")
        self.svc.change_password(riley, "demo", "newpassword42")
        self.svc.login_password("riley", "newpassword42")
        with self.assertRaises(Forbidden):
            self.svc.verify_current_password(riley, "demo")


class TestAdmin(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.svc

    def test_admin_creates_staff_but_not_students(self):
        alex = user(self.app, "u_alex")
        r = self.svc.create_user(alex, "pat.nguyen", "Pat Nguyen", "rn", "", "welcome2026")
        tok, u = self.svc.login_password("pat.nguyen", "welcome2026")
        self.assertEqual((u["org"], u["role"]), ("org_n", "rn"))
        with self.assertRaises(Forbidden):
            self.svc.create_user(alex, "stu.one", "Stu", "student", "", "welcome2026")
        with self.assertRaises(Conflict):
            self.svc.create_user(alex, "pat.nguyen", "Dup", "rn", "", "welcome2026")
        with self.assertRaises(BadInput):
            self.svc.create_user(alex, "x", "Bad", "rn", "", "welcome2026")
        self.svc.set_active(alex, r["user_id"], False)
        self.assertIsNone(self.svc.user_for_token(tok))

    def test_instructor_creates_students_in_school_only(self):
        morgan = user(self.app, "u_morgan")
        self.svc.create_user(morgan, "jamie.s", "Jamie", "student", "Nursing student", "welcome2026")
        self.assertEqual({u["role"] for u in self.svc.list_users(morgan)}, {"student", "instructor"})
        with self.assertRaises(Forbidden):
            self.svc.create_user(morgan, "nurse.x", "X", "charge_rn", "", "welcome2026")
        with self.assertRaises(Forbidden):
            self.svc.list_users(user(self.app, "u_riley"))
        with self.assertRaises(NotFound):
            self.svc.set_active(morgan, "u_riley", False)     # other org


if __name__ == "__main__":
    unittest.main()
