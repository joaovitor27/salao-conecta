from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class CustomPageNumberPagination(PageNumberPagination):
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        meta_data = {}
        if hasattr(self, 'meta_data'):
            meta_data = self.meta_data
        elif hasattr(self.request, 'meta_data'):
            meta_data = self.request.meta_data
        elif hasattr(self.request, 'parser_context'):
            view = self.request.parser_context.get('view')
            if view and hasattr(view, 'get_meta'):
                meta_data = view.get_meta()

        return Response({
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'meta': meta_data,
            'results': data,
        })