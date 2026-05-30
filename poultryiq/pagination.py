from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator


def paginate(request, queryset, per_page=20, page_param="page"):
    paginator = Paginator(queryset, per_page)
    page_number = request.GET.get(page_param, 1)

    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    query = request.GET.copy()
    query.pop(page_param, None)
    querystring = query.urlencode()

    return page_obj, querystring
