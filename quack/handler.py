import webapp2
from google.appengine.ext import ndb
import logging

# Node transitions
class Transition(object):
    def __init__(self, to_node):
        self.to_node = to_node

    def execute(self, dispatcher, curr_visit):
        raise NotImplementedError


class GetMessage(Transition):
    def execute(self, dispatcher, curr_visit):
        curr_visit.next_node = self.to_node.name


class GoTo(Transition):
    def execute(self, dispatcher, curr_visit):
        curr_visit.next_node = self.to_node.name
        curr_visit.put()
        dispatcher.dispatch(curr_visit.user.get().phone_number, None)


class GoBack(GoTo):
    def __init__(self):
        super(GoBack, self).__init__(None)

    def execute(self, dispatcher, curr_visit):
        curr_visit.next_node = self.to_node
        # look up the previous visit for this user
        dispatcher.dispatch(curr_visit.user.get().phone_number, None)


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

    def dispatch(self, user_phone_number, message):
        user = User.query(User.phone_number == user_phone_number).get()
        if user:
            prev_visit = Visit.query(ancestor=user.key).filter(Visit.user == user.key).order(-Visit.created_at).get()

        if prev_visit and prev_visit.next_node:
            logging.info("found prev_visit %s", prev_visit.key.id())

            node = self.graph.get(prev_visit.next_node)
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

        transition = node.visit(curr_visit, prev_app_state, message)

        logging.info("OK great, transition %s to %s", transition.__class__.__name__, transition.to_node.name)
        transition.execute(self, curr_visit)
        curr_visit.put()


# Node structure
class Graph(object):
    def __init__(self):
        self.nodes = {}
        self.start_node = None

    def register(self, node):
        self.nodes[node.name] = node

    def get(self, name):
        return self.nodes[name]


class Node(object):
    def name(self):
        return self.__class__.__name__

    def visit(self, visit, prev_app_state, message):
        self.current_visit = visit
        visit.current_node = self.name
        logging.info("Node %s handling %s", self.name, message)
        transition = self.handle(prev_app_state, message)
        self.current_visit = None
        return transition

    def send(self, message):
        visit = self.current_visit
        msg = Message(body=message, user=visit.user, parent=visit.key)
        msg.put()
        visit.messages.append(msg.key)
        logging.info("Say: %s", message)


class HelpNode(Node):
    help_messages = ["help"]
    def handle(self, state, message):
        assert message in self.help_messages
        self.send("There is no help")
        return GoBack()


class EnterNode(Node):
    def __init__(self, location):
        self.name = location.tag + "_enter"
        self.location = location

    def handle(self, state, message):
        self.send(self.location.description)
        return GetMessage(self.location.choice_node)


class ChoiceNode(Node):
    def __init__(self, location):
        self.name = location.tag + "_choice"
        self.location = location

    def handle(self, state, message):
        choice = self.location.actions.get(message.lower())
        if not choice:
            self.send("I didn't understand that")
            return GetMessage(self)

        self.send(choice[0])
        return choice[1]

# Game abstractions
class Player(BaseModel):
    pass


class Item(BaseModel):
    player = ndb.KeyProperty()
    location = ndb.StringProperty()
    name = ndb.StringProperty()


class Location(object):
    def __init__(self, tag, graph, description=None, actions=None, items=None):
        self.tag = tag
        self.description = description
        self.actions = actions
        self.items = items
        self._build_nodes(graph)

    def go(self):
        return GoTo(self.enter_node)

    def _build_nodes(self, graph):
        self.choice_node = ChoiceNode(self)
        self.enter_node = EnterNode(self)
        graph.register(self.choice_node)
        graph.register(self.enter_node)


world = Graph()

another_room = Location(
    'another_room',
    world,
    description="It is another empty room. There is a heavy DOOR."
)

an_empty_room = Location(
    'an_empty_room',
    world,
    description="You are in an empty room. There is a DOOR.",
    actions={
        "door": ("You open the door", another_room.go())
    }
)
another_room.actions = {
    "door": ("After a struggle, you open the door", an_empty_room.go())
}

world.start_node = an_empty_room.enter_node
