import webapp2
from google.appengine.ext import ndb
import logging
import yaml

# Node transitions
class Transition(object):
    def __init__(self, to_node):
        assert isinstance(to_node, basestring), "to_node should be a string, was %s" % type(to_node)
        self.to_node = to_node

    def execute(self, dispatcher, curr_visit, session):
        raise NotImplementedError


class GetMessage(Transition):
    def execute(self, dispatcher, curr_visit, session):
        curr_visit.next_node = self.to_node
        return session


class GoTo(Transition):
    def execute(self, dispatcher, curr_visit, session):
        curr_visit.next_node = self.to_node
        curr_visit.put()
        session = dispatcher.dispatch(curr_visit.user.get().phone_number, None, session)
        return session


class GoBack(GoTo):
    def __init__(self):
        super(GoBack, self).__init__("")

    def execute(self, dispatcher, curr_visit, session):
        curr_visit.next_node = self.to_node
        # look up the previous visit for this user
        session = dispatcher.dispatch(curr_visit.user.get().phone_number, None, session)
        return session


# Storage primitives
class BaseModel(ndb.Model):
    created_at  = ndb.DateTimeProperty(auto_now_add=True)
    updated_at  = ndb.DateTimeProperty(auto_now=True)


class User(BaseModel):
    phone_number = ndb.StringProperty()


class Message(BaseModel):
    user = ndb.KeyProperty()
    body = ndb.StringProperty()
    direction = ndb.StringProperty()


class Visit(BaseModel):
    current_node = ndb.StringProperty()
    user = ndb.KeyProperty()
    messages = ndb.KeyProperty(kind=Message, repeated=True)
    application_state = ndb.KeyProperty()
    next_node = ndb.StringProperty()


class Dispatcher(object):
    def __init__(self, graph):
        self.graph = graph

    def run(self, user_phone_number, message):
      session = []
      return self.dispatch(user_phone_number, message, session)


    def dispatch(self, user_phone_number, message, session):
        user = User.query(User.phone_number == user_phone_number).get()
        if user:
            prev_visit = Visit.query(ancestor=user.key).filter(Visit.user == user.key).order(-Visit.created_at).get()
        else:
          prev_visit = None

        if prev_visit and prev_visit.next_node:
            logging.info("found prev_visit %s", prev_visit.key.id())

            try:
              node = self.graph.get(prev_visit.next_node)
            except KeyError:
              # bad state; start over
              node = self.graph.start_node
              prev_app_state = None

            if prev_visit.application_state:
                prev_app_state = prev_visit.application_state.get()
            else:
                prev_app_state = None
        else:
            logging.info("first visit! start node")
            # TODO: make this a general start method so the app can bootstrap
            if not user:
                user = User(phone_number=user_phone_number)
                user.put()
            node = self.graph.start_node
            prev_app_state = None

        curr_visit = Visit(user=user.key, parent=user.key)
        curr_visit.put()
        logging.info("Visiting %s with %s", node.name, message)

        if message:
            msg = Message(parent=curr_visit.key, user=user.key, direction="inbound", body=message)
            msg.put()
            curr_visit.messages.append(msg.key)

        transition, session = node.visit(curr_visit, prev_app_state, message, session)

        logging.info("OK great, transition %s to %s", transition.__class__.__name__, transition.to_node)
        session = transition.execute(self, curr_visit, session)
        curr_visit.put()
        return session


# Node structure
class Graph(object):
    def __init__(self):
        self.nodes = {}
        self.start_node = None

    def register(self, node):
        self.nodes[node.name] = node

    def get(self, name):
        return self.nodes[name]

    def build(self, script):
      self.register(HelpNode())
      for i, location_dict in enumerate(script['locations']):
        for location_spec in location_dict.items():
          location = parse_location(location_spec)
          nodes = location.to_nodes()
          map(self.register, nodes)

        # start at the first node of the first room
        if i == 0:
          self.start_node = nodes[0]


class Node(object):
    def name(self):
        return self.__class__.__name__

    def visit(self, visit, prev_app_state, message, session):
        self.current_visit = visit
        self.current_session = session

        visit.current_node = self.name
        logging.info("Node %s handling %s", self.name, message)
        transition = self.handle(prev_app_state, message)
        self.current_visit = None
        return transition, session

    def send(self, message):
        visit = self.current_visit
        msg = Message(body=message, user=visit.user, parent=visit.key)
        msg.put()
        visit.messages.append(msg.key)
        self.current_session.append(msg)
        logging.info("Say: %s", message)


class HelpNode(Node):
  def __init__(self):
    self.name = "help"

  def handle(self, state, message):
    self.send("There is no help")
    return GoBack()


class EnterNode(Node):
    def __init__(self, location):
        self.name = location.tag + "_enter"
        self.location = location

    def handle(self, state, message):
        self.send(self.location.description)
        return GetMessage(self.location.choice_node.name)


class ChoiceNode(Node):
    def __init__(self, location):
        self.name = location.tag + "_choice"
        self.location = location

    def handle(self, state, message):
        effects = self.location.actions.get(message.lower())
        if not effects:
          self.send("I didn't understand that.")
          self.send("You can say: %s" % ", ".join(self.location.actions.keys()))
          return GetMessage(self.name)

        for effect in effects:
          for effect_type, body in effect.items():
            if effect_type == "say":
              self.send(body)
            elif effect_type == "goto":
              return GoTo(body + "_enter")
            elif effect_type == "help":
              return GoTo("help")

        self.send(choice[0])
        return choice[1]

class Player(BaseModel):
    pass

def parse_location(location_spec):
  """ Parse a location from yaml
  expects a tuple ('tag', props)
  room_one:
    description: "there is a DOOR"
    actions:
      door:
        - say: "you open the door"
        - goto: room_two
  """
  location_tag = location_spec[0]
  properties = location_spec[1]
  return Location(location_tag, **properties)



default_actions = {
  "help": [{"help": ""}]
}

class Location(object):
    def __init__(self, tag, description=None, actions=None):
        self.tag = tag
        self.description = description
        self.actions = actions
        self.actions.update(default_actions)

    def go(self):
        return GoTo(self.enter_node)

    def to_nodes(self):
        self.choice_node = ChoiceNode(self)
        self.enter_node = EnterNode(self)
        return [self.enter_node, self.choice_node]


world = Graph()
script_file = open('script.yaml')
script = yaml.load(script_file.read())
world.build(script)
