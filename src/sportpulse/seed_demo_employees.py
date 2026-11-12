"""Crée explicitement 3 comptes fictifs pour tester le cycle QR/code.

Commande: docker compose exec app python -m sportpulse.seed_demo_employees --create
Les mots de passe aléatoires ne sont affichés qu'une seule fois dans le terminal.
Ne met pas ces identifiants en Git ou dans Slack.
"""
from __future__ import annotations
import argparse
import secrets
import time
from sportpulse import employee_portal as portal

SAMPLES = (
    ('EMP-DEMO-QR-001', 'Lina Martin', 'lina.martin.demo@sportpulse.example'),
    ('EMP-DEMO-QR-002', 'Amine Diallo', 'amine.diallo.demo@sportpulse.example'),
    ('EMP-DEMO-QR-003', 'Nora Bernard', 'nora.bernard.demo@sportpulse.example'),
)


def provision() -> list[tuple[str, str, str, str | None]]:
    outputs=[]
    with portal.connect() as conn:
        for employee_id, name, email in SAMPLES:
            if conn.execute('SELECT id FROM accounts WHERE employee_id=? OR email=?',(employee_id,email)).fetchone():
                outputs.append((employee_id,name,email,None))
                continue
            password=secrets.token_urlsafe(22)
            salt=secrets.token_bytes(16)
            cur=conn.execute('''INSERT INTO accounts
                 (employee_id,email,display_name,password_salt,password_digest,is_demo,created_at)
                 VALUES (?,?,?,?,?,1,?)''',
                 (employee_id,email,name,salt.hex(),portal.password_hash(password,salt),int(time.time())))
            portal.seed_demo(conn,cur.lastrowid)
            outputs.append((employee_id,name,email,password))
    return outputs


def main():
    parser=argparse.ArgumentParser(description='SportPulse : comptes fictifs QR')
    parser.add_argument('--create',action='store_true',help='Création explicite, ne touche jamais aux salariés RH')
    args=parser.parse_args()
    if not args.create:
        print('Mode lecture seule. Ajouter --create pour générer les comptes de démonstration.');return
    for employee_id,name,email,password in provision():
        if password is None:
            print(f'{employee_id} | EXISTANT | {email} (mot de passe inchangé)')
        else:
            print(f'{employee_id} | {name} | {email} | mot_de_passe={password}')
    print('Ne publie jamais ces identifiants dans Slack ou Git. Supprime cet affichage de ton historique si besoin.')

if __name__=='__main__':main()
