import webapp2
import logging
import os
from twilio import twiml
from google.appengine.ext.webapp import template
from google.appengine.ext import ndb
import quack
import operator

class MainHandler(webapp2.RequestHandler):
    def get(self):
        tw = str(twiml.Response())
        self.response.content_type = 'application/xml'
        self.response.write(tw)

class WebBase(webapp2.RequestHandler):
    pass

class AdminHandler(WebBase):
    def get(self):
        user = quack.User.query().filter(quack.User.phone_number == "7037919267").get()
        visits = quack.Visit.query(ancestor=user.key).filter(quack.Visit.user == user.key)
        messages = ndb.get_multi([m for v in visits for m in v.messages])
        messages = sorted(messages, key=operator.attrgetter('created_at'))

        template_values = {
            "visits": visits,
            "messages": messages
        }
        path = os.path.join(os.path.dirname(__file__), 'templates/main.html')
        self.response.out.write(template.render(path, template_values))

dispatcher = quack.Dispatcher(quack.world)

class MessageHandler(webapp2.RequestHandler):
    def post(self):
        phone_number = self.request.get('phone_number')
        content = self.request.get('content')

        logging.info("Dispatching %s from %s", content, phone_number)
        dispatcher.dispatch(phone_number, content)

        tw = str(twiml.Response())
        self.response.content_type = 'application/xml'
        self.response.write(tw)

        self.redirect('/admin')

app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/message', MessageHandler),
    ('/admin', AdminHandler)
], debug=True)
