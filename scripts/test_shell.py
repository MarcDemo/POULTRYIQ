from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()
user = User.objects.filter(is_superuser=True).first() or User.objects.filter(is_active=True).first()
print('User:', user.username)

client = Client(SERVER_NAME='127.0.0.1')
client.force_login(user)

pages = [
    ('/birds/', 'Birds'),
    ('/eggrec/', 'Egg Collection'),
    ('/feedrec/', 'Feed Records'),
    ('/finance/expenses/', 'Expenses'),
    ('/health/vaccination/', 'Vaccination'),
    ('/health/view-sickness-reports/', 'Health Records'),
]

all_ok = True
for url, name in pages:
    try:
        r = client.get(url)
        s = r.status_code
        label = 'OK' if s == 200 else 'FAIL'
        if s != 200:
            all_ok = False
        print(label, name, '->', 'HTTP', s)
    except Exception as e:
        all_ok = False
        print('ERR', name, '->', str(e))

print()
print('Result:', 'ALL PASS' if all_ok else 'SOME FAILED')
