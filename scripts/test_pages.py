import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'poultryiq.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

user = User.objects.filter(is_superuser=True).first() or User.objects.filter(is_active=True).first()
if not user:
    print('No user found')
    exit(1)

role_code = getattr(getattr(user, 'role', None), 'code', '(no role)')
print(f'Testing as: {user.username} | role: {role_code}')

from django.test import Client
client = Client()
client.force_login(user)

pages = [
    ('/birds/', 'Birds'),
    ('/eggrec/', 'Egg Collection'),
    ('/feedrec/', 'Feed Records'),
    ('/expenses/', 'Expenses'),
    ('/vaccination/', 'Vaccination'),
]

all_ok = True
for url, name in pages:
    try:
        r = client.get(url)
        status = r.status_code
        ok = 'OK' if status == 200 else 'FAIL'
        if status != 200:
            all_ok = False
        print(f'  [{ok}] {name} ({url}) -> HTTP {status}')
    except Exception as e:
        all_ok = False
        print(f'  [ERR] {name} ({url}) -> {e}')

print()
print('All pages OK!' if all_ok else 'Some pages FAILED - check above.')
