"""Shared repo fixture for e2e/concurrency scripts."""

import os


def make_repo(root: str) -> None:
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "auth.py"), "w") as f:
        f.write(
            "class Authenticator:\n"
            "    \"\"\"Handles user login sessions and token validation.\"\"\"\n\n"
            "    def login(self, user, password):\n"
            "        \"\"\"Verify credentials and create a session.\"\"\"\n"
            "        if not user or not password:\n"
            "            raise ValueError('missing credentials')\n"
            "        token = self._issue_token(user)\n"
            "        return token\n\n"
            "    def _issue_token(self, user):\n"
            "        return f'tok-{user}'\n"
        )
    with open(os.path.join(root, "src", "payments.py"), "w") as f:
        f.write(
            "class Invoice:\n"
            "    \"\"\"Billing document for purchased subscriptions.\"\"\"\n\n"
            "    def total_cents(self):\n"
            "        return sum(l.amount_cents for l in self.lines)\n"
        )
    with open(os.path.join(root, "src", "db.py"), "w") as f:
        f.write(
            "class ConnectionPool:\n"
            "    \"\"\"Database connection pooling.\"\"\"\n\n"
            "    def acquire(self):\n"
            "        return self._free.pop()\n"
        )
    with open(os.path.join(root, "README.md"), "w") as f:
        f.write("# Throwaway repo\n\nUser authentication and billing docs.\n")