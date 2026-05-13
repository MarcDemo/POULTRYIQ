import re

with open('poultry/views.py', encoding='utf-8') as f:
    content = f.read()

# The broken section has duplicate _get_worker_active_batches
# Pattern: two consecutive definitions
broken = (
    '    context = {\n'
    '        "batch_data": batch_data,\n'
    '        "houses": PoultryHouse.objects.filter(is_active=True),\n'
    '        "selected_status": status,\n'
    '        "selected_house": house,\n'
    '        "search_query": search,\n'
    '    }\n'
    '    return render(request, \'birds.html\', context)\n'
    '\n'
    '    context = {\n'
    '\n'
    'def _get_worker_active_batches(user):\n'
    '    return PoultryBatch.objects.filter(\n'
    '\n'
    'def _get_worker_active_batches(user):\n'
    '    return PoultryBatch.objects.filter(\n'
    '        house__in=user.houses.all(),\n'
    '        status=PoultryBatch.Status.ACTIVE,\n'
    '    ).select_related("house").order_by("house__house_code", "batch_code")'
)

fixed = (
    '    context = {\n'
    '        "batch_data": batch_data,\n'
    '        "houses": PoultryHouse.objects.filter(is_active=True),\n'
    '        "breeds": PoultryBatch.objects.exclude(breed="").values_list("breed", flat=True).distinct().order_by("breed"),\n'
    '        "selected_status": status,\n'
    '        "selected_house": house,\n'
    '        "search_query": search,\n'
    '        "selected_breed": breed_filter,\n'
    '    }\n'
    '    return render(request, \'birds.html\', context)\n'
    '\n'
    '\n'
    'def _get_worker_active_batches(user):\n'
    '    return PoultryBatch.objects.filter(\n'
    '        house__in=user.houses.all(),\n'
    '        status=PoultryBatch.Status.ACTIVE,\n'
    '    ).select_related("house").order_by("house__house_code", "batch_code")'
)

if broken in content:
    content = content.replace(broken, fixed, 1)
    with open('poultry/views.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: Fixed birds() context block')
else:
    print('Pattern NOT FOUND. Showing relevant lines:')
    for i, line in enumerate(content.splitlines(), 1):
        if any(kw in line for kw in ['search_query', 'selected_breed', '_get_worker_active']):
            print(f'{i:4d}: {line}')
