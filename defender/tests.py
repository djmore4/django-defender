import random
import string
import time

from mock import patch
import mockredis

from django.test import TestCase
from django.contrib.auth.models import User
from django.core.urlresolvers import NoReverseMatch
from django.core.urlresolvers import reverse

from .connection import parse_redis_url
from . import utils
from . import config

mocked_redis = mockredis.mock_strict_redis_client()


def mock_get_connection():
    if config.MOCK_REDIS:  # pragma: no cover
        return mocked_redis  # pragma: no cover
    else:  # pragma: no cover
        from .connection import get_redis_connection  # pragma: no cover
        return get_redis_connection()  # pragma: no cover


# Django >= 1.7 compatibility
try:
    LOGIN_FORM_KEY = '<form action="/admin/" method="post" id="login-form">'
    ADMIN_LOGIN_URL = reverse('admin:login')
except NoReverseMatch:
    ADMIN_LOGIN_URL = reverse('admin:index')
    LOGIN_FORM_KEY = 'this_is_the_login_form'


@patch('defender.connection.get_redis_connection', mock_get_connection)
@patch('defender.utils.redis_server', mock_get_connection())
class AccessAttemptTest(TestCase):
    """ Test case using custom settings for testing
    """
    VALID_USERNAME = 'valid'
    LOCKED_MESSAGE = 'Account locked: too many login attempts.'

    def _get_random_str(self):
        """ Returns a random str """
        chars = string.ascii_uppercase + string.digits

        return ''.join(random.choice(chars) for x in range(20))

    def _login(self, is_valid=False, user_agent='test-browser'):
        """ Login a user. A valid credential is used when is_valid is True,
           otherwise it will use a random string to make a failed login.
        """
        username = self.VALID_USERNAME if is_valid else self._get_random_str()

        response = self.client.post(ADMIN_LOGIN_URL, {
            'username': username,
            'password': username,
            LOGIN_FORM_KEY: 1,
        }, HTTP_USER_AGENT=user_agent)

        return response

    def setUp(self):
        """ Create a valid user for login
        """
        self.user = User.objects.create_superuser(
            username=self.VALID_USERNAME,
            email='test@example.com',
            password=self.VALID_USERNAME,
        )

    def tearDown(self):
        """ clean up the db """
        mock_get_connection().flushdb()

    def test_failure_limit_once(self):
        """ Tests the login lock trying to login one more time
        than failure limit
        """
        for i in range(0, config.FAILURE_LIMIT):
            response = self._login()
            # Check if we are in the same login page
            self.assertContains(response, LOGIN_FORM_KEY)

        # So, we shouldn't have gotten a lock-out yet.
        # But we should get one now
        response = self._login()
        self.assertContains(response, self.LOCKED_MESSAGE)

        # doing a get should also get locked out message
        response = self.client.get(ADMIN_LOGIN_URL)
        self.assertContains(response, self.LOCKED_MESSAGE)

    def test_failure_limit_many(self):
        """ Tests the login lock trying to login a lot of times more
        than failure limit
        """
        for i in range(0, config.FAILURE_LIMIT):
            response = self._login()
            # Check if we are in the same login page
            self.assertContains(response, LOGIN_FORM_KEY)

        # So, we shouldn't have gotten a lock-out yet.
        # But we should get one now
        for i in range(0, random.randrange(1, 10)):
            # try to log in a bunch of times
            response = self._login()
            self.assertContains(response, self.LOCKED_MESSAGE)

        # doing a get should also get locked out message
        response = self.client.get(ADMIN_LOGIN_URL)
        self.assertContains(response, self.LOCKED_MESSAGE)

    def test_valid_login(self):
        """ Tests a valid login for a real username
        """
        response = self._login(is_valid=True)
        self.assertNotContains(response, LOGIN_FORM_KEY, status_code=302)

    def test_cooling_off(self):
        """ Tests if the cooling time allows a user to login
        """
        self.test_failure_limit_once()
        # Wait for the cooling off period
        time.sleep(config.COOLOFF_TIME)

        if config.MOCK_REDIS:
            # mock redis require that we expire on our own
            mock_get_connection().do_expire()  # pragma: no cover
        # It should be possible to login again, make sure it is.
        self.test_valid_login()

    def test_cooling_off_for_trusted_user(self):
        """ Test the cooling time for a trusted user
        """
        # Try the cooling off time
        self.test_cooling_off()

    def test_long_user_agent_valid(self):
        """ Tests if can handle a long user agent
        """
        long_user_agent = 'ie6' * 1024
        response = self._login(is_valid=True, user_agent=long_user_agent)
        self.assertNotContains(response, LOGIN_FORM_KEY, status_code=302)

    def test_long_user_agent_not_valid(self):
        """ Tests if can handle a long user agent with failure
        """
        long_user_agent = 'ie6' * 1024
        for i in range(0, config.FAILURE_LIMIT + 1):
            response = self._login(user_agent=long_user_agent)

        self.assertContains(response, self.LOCKED_MESSAGE)

    def test_reset_ip(self):
        """ Tests if can reset an ip address
        """
        # Make a lockout
        self.test_failure_limit_once()

        # Reset the ip so we can try again
        utils.reset_failed_attempts(ip='127.0.0.1')

        # Make a login attempt again
        self.test_valid_login()

    @patch('defender.config.LOCKOUT_URL', 'http://localhost/othe/login/')
    def test_failed_login_redirect_to_URL(self):
        """ Test to make sure that after lockout we send to the correct
        redirect URL """

        for i in range(0, config.FAILURE_LIMIT):
            response = self._login()
            # Check if we are in the same login page
            self.assertContains(response, LOGIN_FORM_KEY)

        # So, we shouldn't have gotten a lock-out yet.
        # But we should get one now, check redirect make sure it is valid.
        response = self._login()
        self.assertEquals(response.status_code, 302)
        self.assertEquals(response['Location'], 'http://localhost/othe/login/')

        # doing a get should also get locked out message
        response = self.client.get(ADMIN_LOGIN_URL)
        self.assertEquals(response.status_code, 302)
        self.assertEquals(response['Location'], 'http://localhost/othe/login/')

    @patch('defender.config.LOCKOUT_URL', '/o/login/')
    def test_failed_login_redirect_to_URL_local(self):
        """ Test to make sure that after lockout we send to the correct
        redirect URL """

        for i in range(0, config.FAILURE_LIMIT):
            response = self._login()
            # Check if we are in the same login page
            self.assertContains(response, LOGIN_FORM_KEY)

        # So, we shouldn't have gotten a lock-out yet.
        # But we should get one now, check redirect make sure it is valid.
        response = self._login()
        self.assertEquals(response.status_code, 302)
        self.assertEquals(response['Location'], 'http://testserver/o/login/')

        # doing a get should also get locked out message
        response = self.client.get(ADMIN_LOGIN_URL)
        self.assertEquals(response.status_code, 302)
        self.assertEquals(response['Location'], 'http://testserver/o/login/')

    def test_is_valid_ip(self):
        """ Test the is_valid_ip() method
        """
        self.assertEquals(utils.is_valid_ip('192.168.0.1'), True)
        self.assertEquals(utils.is_valid_ip('130.80.100.24'), True)
        self.assertEquals(utils.is_valid_ip('8.8.8.8'), True)
        self.assertEquals(utils.is_valid_ip('127.0.0.1'), True)
        self.assertEquals(utils.is_valid_ip('fish'), False)
        self.assertEquals(utils.is_valid_ip(None), False)
        self.assertEquals(utils.is_valid_ip(''), False)

    def test_parse_redis_url(self):
        """ test the parse_redis_url method """
        # full regular
        conf = parse_redis_url("redis://user:password@localhost2:1234/2")
        self.assertEquals(conf.get('HOST'), 'localhost2')
        self.assertEquals(conf.get('DB'), 2)
        self.assertEquals(conf.get('PASSWORD'), 'password')
        self.assertEquals(conf.get('PORT'), 1234)

        # full non local
        conf = parse_redis_url("redis://user:pass@www.localhost.com:1234/2")
        self.assertEquals(conf.get('HOST'), 'www.localhost.com')
        self.assertEquals(conf.get('DB'), 2)
        self.assertEquals(conf.get('PASSWORD'), 'pass')
        self.assertEquals(conf.get('PORT'), 1234)

        # no user name
        conf = parse_redis_url("redis://password@localhost2:1234/2")
        self.assertEquals(conf.get('HOST'), 'localhost2')
        self.assertEquals(conf.get('DB'), 2)
        self.assertEquals(conf.get('PASSWORD'), None)
        self.assertEquals(conf.get('PORT'), 1234)

        # no user name 2 with colon
        conf = parse_redis_url("redis://:password@localhost2:1234/2")
        self.assertEquals(conf.get('HOST'), 'localhost2')
        self.assertEquals(conf.get('DB'), 2)
        self.assertEquals(conf.get('PASSWORD'), 'password')
        self.assertEquals(conf.get('PORT'), 1234)

        # Empty
        conf = parse_redis_url(None)
        self.assertEquals(conf.get('HOST'), 'localhost')
        self.assertEquals(conf.get('DB'), 0)
        self.assertEquals(conf.get('PASSWORD'), None)
        self.assertEquals(conf.get('PORT'), 6379)

        # no db
        conf = parse_redis_url("redis://:password@localhost2:1234")
        self.assertEquals(conf.get('HOST'), 'localhost2')
        self.assertEquals(conf.get('DB'), 0)
        self.assertEquals(conf.get('PASSWORD'), 'password')
        self.assertEquals(conf.get('PORT'), 1234)

        # no password
        conf = parse_redis_url("redis://localhost2:1234/0")
        self.assertEquals(conf.get('HOST'), 'localhost2')
        self.assertEquals(conf.get('DB'), 0)
        self.assertEquals(conf.get('PASSWORD'), None)
        self.assertEquals(conf.get('PORT'), 1234)