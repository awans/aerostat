import webapp2
from google.appengine.ext import ndb
import logging
import yaml
import datetime

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
        curr_visit.transition_executed = True
        curr_visit.put()
        return session


class GoTo(Transition):
    def execute(self, dispatcher, curr_visit, session):
        curr_visit.next_node = self.to_node
        curr_visit.put()
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
    sleep_until = ndb.DateTimeProperty()
    transition_executed = ndb.BooleanProperty()


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
            if prev_visit.sleep_until and prev_visit.sleep_until > datetime.datetime.now():
              return session
            else:
              prev_visit.transition_executed = True

            logging.info("found prev_visit %s", prev_visit.key.id())

            try:
              node = self.graph.get(prev_visit.next_node)
            except KeyError:
              logging.error("No next node found -- wanted %s", prev_visit.next_node)
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

        curr_visit = Visit(user=user.key, parent=user.key, transition_executed=False)
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
        self.locations = []
        self.start_node = None

    def register(self, node):
        self.nodes[node.name] = node

    def get(self, name):
        return self.nodes[name]

    def build(self, script):
      for i, location_dict in enumerate(script['locations']):
        for location_spec in location_dict.items():
          location = parse_location(location_spec)
          self.locations.append(location)
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

    def delay(self, delay_spec):
        if not delay_spec:
            return
        now = datetime.datetime.now()
        number, unit = int(delay_spec[:-1]), delay_spec[-1:]
        if unit == "h":
          multiple = 60 * 60
        elif unit == "m":
          multiple = 60
        elif unit == "s":
          multiple = 1
        else:
          logging.error("INVALID SPEC")
        sleep_until = now + datetime.timedelta(seconds=number * multiple)
        self.current_visit.sleep_until = sleep_until


class Effect(object):
  def __repr__(self):
    return self.__class__.__name__


class Say(Effect):
  def __repr__(self):
    return "Say(%s)" % self.message

  def __init__(self, message, delay):
    self.message = message
    self.delay = delay


class Go(Effect):
  def __init__(self, to_location, delay):
    self.to_location = to_location
    self.delay = delay


class Action(object):
  def __repr__(self):
    return "%s - %s" % (self.tag, self.effects)

  def __init__(self, tag, effect_list):
    self.tag = tag
    self.effects = effect_list


class EffectNode(Node):
  def __repr__(self):
    return "%s:%s" % (self.name, self.next_node.name if self.next_node else None)


  def __init__(self, name, effect, next_node, choice_node_next=False):
    self.name = name
    self.effect = effect
    self.next_node = next_node
    self.choice_node_next = choice_node_next

  def handle(self, state, message):
      effect = self.effect
      self.delay(effect.delay)
      if isinstance(effect, Say):
        self.send(effect.message)
        if not self.next_node:
          logging.error(self.name)
        if self.choice_node_next:
          return GetMessage(self.next_node.name)
        else:
          return GoTo(self.next_node.name)
      if isinstance(effect, Go):
        return GoTo(effect.to_location + "_enter_1")


class ChoiceNode(Node):
    def __init__(self, location, effect_chains):
        self.name = location.tag + "_choice"
        self.effect_chains = effect_chains

    def handle(self, state, message):
        effects = self.effect_chains.get(message.lower())
        if not effects:
          self.send("I didn't understand that.")
          self.send("You can say: %s" % ", ".join(self.effect_chains.keys()))
          return GetMessage(self.name)
        return GoTo(effects[0].name)


class Player(BaseModel):
    pass


class InvalidSpec(Exception):
  pass


def parse_effect_list(effect_spec_list):
  if isinstance(effect_spec_list, basestring):
    return [Say(effect_spec_list, None)]
  if not isinstance(effect_spec_list, list):
    raise InvalidSpec("Expected a list; got %s" % effect_spec_list)
  return map(parse_effect, effect_spec_list)


def parse_effect(effect_spec):
  """ parse an effect """
  delay = None
  if "delay" in effect_spec:
    delay = effect_spec.pop("delay")

  try:
    key = effect_spec.keys()[0]
    val = effect_spec.values()[0]
  except:
    print effect_spec
    raise
  if key == "say":
    return Say(val, delay)
  elif key == "goto":
    return Go(val, delay)


def parse_actions(actions_spec):
  if not actions_spec:
    return []
  actions = []
  for key, val in actions_spec.items():
    actions.append(Action(key, parse_effect_list(val)))
  return actions


def parse_location(location_spec):
  """ Parse a location from yaml
  expects a tuple ('tag', props)
  room_one:
    enter:
      - say: "there is a DOOR"
    actions:
      door:
        - say: "you open the door"
        - goto: room_two
  """
  location_tag = location_spec[0]
  properties = location_spec[1]
  try:
    enter = parse_effect_list(properties['enter'])
    actions = parse_actions(properties.get('actions', None))
  except:
    print location_spec
    raise
  return Location(location_tag, enter=enter, actions=actions)


class HelpAction(Action):
  def __init__(self):
    self.tag = "help"
    self.effects = [Go("help", None)]


default_actions = {
  "help": HelpAction()
}


def make_effect_nodes(effect_list, prefix=None):
  prev_node = None
  nodes = []
  total = len(effect_list)
  for i, effect in enumerate(reversed(effect_list)):
    name = "%s_%s" % (prefix, (total - i))
    if prev_node:
      node = EffectNode(name, effect, next_node=prev_node)
    else:
      node = EffectNode(name, effect, None)
    nodes.append(node)
    prev_node = node
  return list(reversed(nodes))


class Location(object):
    def __repr__(self):
      return "Location(%s, enter: %s, actions: %s)" % (self.tag, self.enter, self.actions)

    def __init__(self, tag, enter=None, actions=None):
        self.tag = tag
        self.enter = enter
        self.actions = {a.tag: a for a in actions}

    def go(self):
        return GoTo(self.enter_node)

    def to_nodes(self):
        nodes = [None]
        action_chains = {}
        for action in self.actions.values():
            effect_nodes = make_effect_nodes(action.effects, prefix="%s_%s" % (self.tag, action.tag))
            nodes += effect_nodes
            action_chains[action.tag] = effect_nodes
        choice_node = ChoiceNode(self, action_chains)
        nodes.append(choice_node)

        enter_effect_nodes = make_effect_nodes(self.enter, prefix="%s_enter" %(self.tag))
        enter_effect_nodes[-1].next_node = choice_node
        enter_effect_nodes[-1].choice_node_next = True

        self.enter_node = enter_effect_nodes[0]
        nodes += enter_effect_nodes[1:]
        nodes[0] = self.enter_node
        return nodes


world = Graph()
script_file = open('script.yaml')
script = yaml.load(script_file.read())
world.build(script)
