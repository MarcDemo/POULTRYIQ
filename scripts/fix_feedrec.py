with open('poultry/views.py', encoding='utf-8') as f:
    content = f.read()

# The broken feedrec block - find it and replace with the correct one
broken = '''def feedrec(request):
    records = FeedRecord.objects.select_related(
        "batch__house", "recorded_by", "reviewed_by"
    ).filter(sickness_report__isnull=True)

    # filters
    f_date = request.GET.get("date", "").strip()
    f_house = request.GET.get("house", "").strip()
    f_feed_type = request.GET.get("feed_type", "").strip()
    f_status = request.GET.get("status", "").strip()

    context = {
        "batch_data": batch_data,
        "houses": PoultryHouse.objects.filter(is_active=True),
        "breeds": PoultryBatch.objects.exclude(breed="").values_list("breed", flat=True).distinct().order_by("breed"),
        "selected_status": status,
        "selected_house": house,
        "search_query": search,
        "selected_breed": breed_filter,
    }
    return render(request, 'birds.html', context)

def _get_worker_active_batches(user):
    return PoultryBatch.objects.filter(
        house__in=user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")
        "f_date": f_date,
        "f_house": f_house,
        "f_feed_type": f_feed_type,
        "f_status": f_status,
        "approval_statuses": ApprovalStatus.choices,
    })'''

fixed = '''def feedrec(request):
    records = FeedRecord.objects.select_related(
        "batch__house", "recorded_by", "reviewed_by"
    ).filter(sickness_report__isnull=True)

    # filters
    f_date = request.GET.get("date", "").strip()
    f_house = request.GET.get("house", "").strip()
    f_feed_type = request.GET.get("feed_type", "").strip()
    f_status = request.GET.get("status", "").strip()

    if f_date:
        try:
            records = records.filter(record_date=date.fromisoformat(f_date))
        except ValueError:
            pass
    if f_house:
        records = records.filter(batch__house__pk=f_house)
    if f_feed_type:
        records = records.filter(feed_type=f_feed_type)
    if f_status:
        records = records.filter(status=f_status)

    records = records.order_by("-record_date", "-feed_id")
    houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code")

    return render(request, 'feed_rec.html', {
        "records": records,
        "houses": houses,
        "feed_types": FeedRecord.FeedType.choices,
        "f_date": f_date,
        "f_house": f_house,
        "f_feed_type": f_feed_type,
        "f_status": f_status,
        "approval_statuses": ApprovalStatus.choices,
    })'''

if broken in content:
    content = content.replace(broken, fixed, 1)
    with open('poultry/views.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: Fixed feedrec()')
else:
    print('Pattern NOT found. Showing lines 955-1000:')
    lines = content.splitlines()
    for i, line in enumerate(lines[954:1005], 955):
        print(f'{i:4d}: {repr(line)}')
