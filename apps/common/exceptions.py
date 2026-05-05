from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def safe_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        return response

    return Response(
        {"detail": "Ocurrio un error interno. Intenta nuevamente mas tarde."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
