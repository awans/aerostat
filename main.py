import webapp2
import logging
import os
from twilio import twiml
from twilio.rest import TwilioRestClient
from google.appengine.ext.webapp import template
from google.appengine.ext import ndb
import pitch
import operator
import phonenumbers
import pprint
import ConfigParser, os
import datetime

dispatcher = pitch.Dispatcher(pitch.world)

config = ConfigParser.ConfigParser()
config.readfp(open('creds.ini'))


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


class UserHandler(WebBase):
  def get(self, phone_number):
    user = pitch.User.query().filter(pitch.User.phone_number == phone_number).get()
    if user:
      visits = pitch.Visit.query(ancestor=user.key).filter(pitch.Visit.user == user.key).order(pitch.Visit.created_at)
    else:
      visits = []
    if not user:
      return

    template_values = {
      "user": {
        "phone_number": user.phone_number,
      },
      "visits": [{
        "current_node": visit.current_node,
        "next_node": visit.next_node,
        "sleep_until": visit.sleep_until,
        "transition_executed": visit.transition_executed,
        "messages": ndb.get_multi(visit.messages)}
        for visit in visits]
    }
    path = os.path.join(os.path.dirname(__file__), 'templates/user.html')
    self.response.out.write(template.render(path, template_values))


class AdminHandler(WebBase):
    def get(self):
      users = pitch.User.query().fetch(1000)
      template_values = {
        "users": [{"phone_number": user.phone_number} for user in users]
      }
      path = os.path.join(os.path.dirname(__file__), 'templates/admin.html')
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
    self.redirect(self.request.referrer)


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


class CronHandler(webapp2.RequestHandler):
  def get(self):
    now = datetime.datetime.now()
    visits_to_wake_up = (
      pitch.Visit.query()
      .filter(pitch.Visit.sleep_until < now)
      .filter(pitch.Visit.sleep_until != None)
      .filter(pitch.Visit.transition_executed == False)
    ).fetch(100)
    logging.info("Cron found: %s", visits_to_wake_up)
    for visit in visits_to_wake_up:
      user = visit.user.get()
      session = dispatcher.run(user.phone_number, None)

      reply = []
      for msg in session:
        reply.append(msg.body)
        msg.put()

      message = "\n".join(reply)
      client = TwilioRestClient(config.get('Twilio', 'account_sid'), config.get('Twilio', 'auth_token'))
      resp = client.messages.create(to=user.phone_number, from_="8317774824", body=message)
      visit.put()
      logging.info("Cron said %s to %s with %s", message, user, resp)


class IntroHandler(webapp2.RequestHandler):
  def post(self):
    phone_number = self.request.get('phone_number')
    session = dispatcher.run(phone_number, "")

    reply = []
    for msg in session:
      reply.append(msg.body)
      msg.put()

    message = "\n".join(reply)
    client = TwilioRestClient(config.get('Twilio', 'account_sid'), config.get('Twilio', 'auth_token'))
    resp = client.messages.create(to=phone_number, from_="8317774824", body=message)
    logging.info("Intro sent to %s", phone_number)
    self.redirect('/admin')




app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/message', MessageHandler),
    ('/reset', ResetHandler),
    ('/twilio', TwilioHandler),
    ('/admin', AdminHandler),
    ('/intro', IntroHandler),
    webapp2.Route('/admin/users/<phone_number>', UserHandler, 'user-detail'),
    ('/validate', ValidateHandler),
    ('/cron', CronHandler),
], debug=True)
