import webapp2
from google.appengine.ext import ndb

# Node transitions
class Transition(object):
    def __init__(self, message, to_node):
        self.to_node = to_node
        self.message = message


class GetMessage(Transition):
    pass


class GoTo(Transition):
    def __init__(self, message, to_node):
        self.is_immediate = True
        super(self, GoTo).__init__(message, to_node)


class GoBack(GoTo):
    def __init__(self, message):
        self.go_back = True
        super(self, GoBack).__init__(message, None)


# Storage primitives
class BaseModel(ndb.Model)
    created_at  = DateTimeProperty(auto_now_add=True)
    updated_at  = DateTimeProperty(auto_now=True)


class Message(BaseModel):
    from_number = StringProperty()
    to_number = StringProperty()
    body = StringProperty()
    direction = StringProperty()


class Visit(BaseModel):
    current_node = StringProperty()
    user_phone_number = StringProperty()
    messages = KeyProperty(kind=Message, repeated=True)
    application_state = KeyProperty()
    next_node = StringProperty()


class Dispatcher(object):
    def __init__(self, graph):
        self.graph = graph

    def dispatch(self, user_phone_number):
        visit = Visit.query(Visit.user_phone_number == from_).order(+Visit.created_at).get()
        node = self.graph.get(visit.next_node)


class Handler(webapp2.RequestHandler):
    def post(self):
        from_ = self.request.get('FromNumber')
        dispatcher.dispatch(from_)

        tw = str(twiml.Response())
        self.response.content_type = 'application/xml'
        self.response.write(tw)


# Node structure
class Graph(object):
    nodes = {}
    def register(self, node):
        self.nodes[node.name()] = node

    def get(self, name):
        return self.nodes[name]



class Node(object):
    def name(self):
        return self.__class__.__name__


class HelpNode(Node):
    help_messages = ["help"]
    def handle(self, state, message):
        assert message in self.help_messages
        return GoBack("There is no help")


class EnterNode(Node):
    def __init__(self, tag, description, choice_node):
        self.name = tag + "_enter"
        self.description = description
        self.choice_node = choice_node

    def handle(self, state, message):
        return GetMessage(self.description, self.choice_node)


# Game abstractions
class Player(BaseModel):
    pass


class Item(BaseModel):
    player = KeyProperty()
    location = StringProperty()
    name = StringProperty()


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
        self.choice_node = ChoiceNode(self.actions, self.items)
        self.enter_node = EnterNode(self.description, self.items, self.choice_node)
        graph.register(self.choice_node)
        graph.register(self.enter_node)


world = Graph()

another_room = Location(
    'another_room',
    graph,
    description="It is another empty room. There is a DOOR."
    actions={
        "door": an_empty_room.go()
    }
)

an_empty_room = Location(
    'an_empty_room',
    graph,
    description="You are in an empty room. There is a DOOR."
    actions={
        "door": another_room.go()
    }
)
