import webapp2
import logging
import os
from twilio import twiml
from google.appengine.ext.webapp import template
from google.appengine.ext import ndb
import pitch
import operator
import phonenumbers
import pprint

dispatcher = pitch.Dispatcher(pitch.world)

class MainHandler(webapp2.RequestHandler):
    def get(self):
        tw = str(twiml.Response())
        self.response.content_type = 'application/xml'
        self.response.write(tw)

class WebBase(webapp2.RequestHandler):
    pass


class ValidateHandler(WebBase):
  def get(self):
    self.response.out.write("<pre>" + pprint.pformat(pitch.script) + "</pre>")
    self.response.out.write("<pre>" + pprint.pformat(pitch.world.locations) + "</pre>")
    self.response.out.write("<pre>" + pprint.pformat(pitch.world.nodes) + "</pre>")


class AdminHandler(WebBase):
    def get(self):
        user = pitch.User.query().filter(pitch.User.phone_number == "7037919267").get()
        if user:
          visits = pitch.Visit.query(ancestor=user.key).filter(pitch.Visit.user == user.key)
          messages = ndb.get_multi([m for v in visits for m in v.messages])
          messages = sorted(messages, key=operator.attrgetter('created_at'))
        else:
          visits = []
          messages = []

        template_values = {
            "visits": visits,
            "messages": messages
        }
        path = os.path.join(os.path.dirname(__file__), 'templates/main.html')
        self.response.out.write(template.render(path, template_values))


class MessageHandler(webapp2.RequestHandler):
  def post(self):
    phone_number = self.request.get('phone_number')
    content = self.request.get('content')
    logging.info("Dispatching %s from %s", content, phone_number)
    session = dispatcher.run(phone_number, content)
    for msg in session:
      msg.put()
    tw = str(twiml.Response())
    self.response.content_type = 'application/xml'
    self.response.write(tw)
    self.redirect('/admin')


class TwilioHandler(webapp2.RequestHandler):
  def post(self):
    content = self.request.get('Body')
    raw_phone_number = self.request.get('From')
    phone_number = str(phonenumbers.parse(raw_phone_number, None).national_number)
    logging.info("Dispatching %s from %s", content, phone_number)
    session = dispatcher.run(phone_number, content)
    resp = twiml.Response()

    reply = []
    for msg in session:
      reply.append(msg.body)
      msg.put()

    resp.message("\n".join(reply))
    self.response.content_type = 'application/xml'
    self.response.write(str(resp))

class ResetHandler(webapp2.RequestHandler):
  def post(self):
    phone_number = self.request.get('phone_number')
    user = pitch.User.query(pitch.User.phone_number == phone_number).get()
    keys = []
    for visit in pitch.Visit.query(ancestor=user.key).filter(pitch.Visit.user == user.key).fetch():
      keys.append(visit.key)
    keys.append(user.key)
    ndb.delete_multi(keys)
    self.redirect('/admin')


app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/message', MessageHandler),
    ('/reset', ResetHandler),
    ('/twilio', TwilioHandler),
    ('/admin', AdminHandler),
    ('/validate', ValidateHandler),
], debug=True)
