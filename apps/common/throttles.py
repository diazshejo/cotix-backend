from rest_framework.throttling import SimpleRateThrottle


class BaseScopedThrottle(SimpleRateThrottle):
    scope = None

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginThrottle(BaseScopedThrottle):
    scope = "login"


class RegisterThrottle(BaseScopedThrottle):
    scope = "register"


class SendEmailThrottle(SimpleRateThrottle):
    scope = "send_email"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = str(request.user.pk)
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class PublicQuoteViewThrottle(BaseScopedThrottle):
    scope = "public_quote_view"


class PublicQuoteRespondThrottle(BaseScopedThrottle):
    scope = "public_quote_respond"
