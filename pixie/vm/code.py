py_object = object
import pixie.vm.object as object
from pixie.vm.object import affirm
from pixie.vm.primitives import nil, true, false
from rpython.rlib.rarithmetic import r_uint
from rpython.rlib.jit import elidable, elidable_promote, promote
import rpython.rlib.jit as jit
import pixie.vm.rt as rt


BYTECODES = ["LOAD_CONST",
             "ADD",
             "EQ",
             "INVOKE",
             "TAIL_CALL",
             "DUP_NTH",
             "RETURN",
             "COND_BR",
             "JMP",
             "CLOSED_OVER",
             "MAKE_CLOSURE",
             "SET_VAR",
             "POP",
             "DEREF_VAR",
             "INSTALL",
             "RECUR",
             "LOOP_RECUR",
             "ARG",
             "PUSH_SELF",
             "POP_UP_N",
             "MAKE_MULTI_ARITY",
             "MAKE_VARIADIC"]

for x in range(len(BYTECODES)):
    globals()[BYTECODES[x]] = r_uint(x)


@jit.unroll_safe
def resize_list(lst, new_size):
    """'Resizes' a list, via reallocation and copy"""
    affirm(len(lst) < new_size, u"New list must be larger than old list")
    new_list = [None] * new_size
    i = r_uint(0)
    while i < len(lst):
        new_list[i] = lst[i]
        i += 1
    return new_list

@jit.unroll_safe
def list_copy(from_lst, from_loc, to_list, to_loc, count):
    from_loc = r_uint(from_loc)
    to_loc = r_uint(to_loc)
    count = r_uint(count)

    i = r_uint(0)
    while i < count:
        to_list[to_loc + i] = from_lst[from_loc+i]
        i += 1
    return to_list

@jit.unroll_safe
def slice_to_end(from_list, start_pos):
    start_pos = r_uint(start_pos)
    items_to_copy = len(from_list) - start_pos
    new_lst = [None] * items_to_copy
    list_copy(from_list, start_pos, new_lst, 0, items_to_copy)
    return new_lst

@jit.unroll_safe
def slice_from_start(from_list, count, extra=r_uint(0)):
    new_lst = [None] * (count + extra)
    list_copy(from_list, 0, new_lst, 0, count)
    return new_lst

# class TailCall(object.Object):
#     _type = object.Type("TailCall")
#     __immutable_fields_ = ["_f", "_args"]
#     def __init__(self, f, args):
#         self._f = f
#         self._args = args
#
#     def run(self):
#         return self._f._invoke(self._args)


class BaseCode(object.Object):
    def __init__(self):
        assert isinstance(self, BaseCode)
        self._is_macro = False

    def set_macro(self):
        self._is_macro = True

    def is_macro(self):
        return self._is_macro

    def _invoke(self, args):
        raise NotImplementedError()

    def get_consts(self):
        raise NotImplementedError()

    def get_bytecode(self):
        raise NotImplementedError()

    @elidable_promote()
    def stack_size(self):
        return 0

    def invoke(self, args):
        result = self._invoke(args)
        return result


class MultiArityFn(BaseCode):
    _type = object.Type(u"pixie.stdlib.MultiArityFn")

    _immutable_fields_ = ["_arities[*]", "_required_arity", "_rest_fn"]

    def type(self):
        return MultiArityFn._type

    def __init__(self, arities, required_arity=0, rest_fn=None):
        BaseCode.__init__(self)
        self._arities = arities
        self._required_arity = required_arity
        self._rest_fn = rest_fn

    @elidable_promote()
    def get_fn(self, arity):
        f = self._arities.get(arity, None)
        if f is not None:
            return f
        if self._rest_fn is not None and arity >= self._required_arity:
            return self._rest_fn

        acc = []
        for x in self._arities:
            acc.append(unicode(str(x)))

        if self._rest_fn:
            acc.append(u" or more")

        affirm(False, u"Wrong number of args to fn: got " + unicode(str(arity)) + u" expected " + u",".join(acc))

    def _invoke(self, args):
        return self.get_fn(len(args)).invoke(args)




class NativeFn(BaseCode):
    """Wrapper for a native function"""
    _type = object.Type(u"NativeFn")

    def __init__(self):
        BaseCode.__init__(self)

    def type(self):
        return NativeFn._type

    def _invoke(self, args):
        return self.inner_invoke(args)

    def inner_invoke(self, args):
        raise NotImplementedError()


class Code(BaseCode):
    """Interpreted code block. Contains consts and """
    _type = object.Type(u"Code")
    __immutable_fields__ = ["_consts[*]", "_bytecode", "_stack_size"]

    def type(self):
        return Code._type

    def __init__(self, name, bytecode, consts, stack_size, debug_points):
        BaseCode.__init__(self)
        self._bytecode = bytecode
        self._consts = consts
        self._name = name
        self._stack_size = stack_size
        self._debug_points = debug_points

    def get_debug_points(self):
        return self._debug_points

    def _invoke(self, args):
        try:
            return interpret(self, args)
        except object.WrappedException as ex:
            ex._ex._trace.append(object.PixieCodeInfo(self._name))
            raise

    @elidable_promote()
    def get_consts(self):
        return self._consts

    @elidable_promote()
    def get_bytecode(self):
        return self._bytecode

    @elidable_promote()
    def stack_size(self):
        return self._stack_size

    @elidable_promote()
    def get_base_code(self):
        return self



class VariadicCode(BaseCode):
    __immutable_fields__ = ["_required_arity", "_code"]
    _type = object.Type(u"pixie.stdlib.VariadicCode")

    def type(self):
        return VariadicCode._type

    def __init__(self, code, required_arity):
        BaseCode.__init__(self)
        self._required_arity = r_uint(required_arity)
        self._code = code

    def _invoke(self, args):
        from pixie.vm.array import array
        argc = len(args)
        if self._required_arity == 0:
            return self._code.invoke([array(args)])
        if argc == self._required_arity:
            new_args = resize_list(args, len(args) + 1)
            new_args[len(args)] = array([])
            return self._code.invoke(new_args)
        elif argc > self._required_arity:
            start = slice_from_start(args, self._required_arity, 1)
            rest = slice_to_end(args, self._required_arity)
            start[self._required_arity] = array(rest)
            return self._code.invoke(start)
        affirm(False, u"Got " + unicode(str(argc)) + u" arg(s) need at least " + unicode(str(self._required_arity)))

class Closure(BaseCode):
    _type = object.Type(u"Closure")
    __immutable_fields__ = ["_closed_overs[*]", "_code"]
    def type(self):
        return Closure._type

    def __init__(self, code, closed_overs):
        BaseCode.__init__(self)
        affirm(isinstance(code, Code), u"Code argument to Closure must be an instance of Code")
        self._code = code
        self._closed_overs = closed_overs

    def _invoke(self, args):
        try:
            return interpret(self, args)
        except object.WrappedException as ex:
            ex._ex._trace.append(object.PixieCodeInfo(self._code._name))
            raise

    def get_closed_over(self, idx):
        return self._closed_overs[idx]

    def get_consts(self):
        return self._code.get_consts()

    def get_bytecode(self):
        return self._code.get_bytecode()

    def stack_size(self):
        return self._code.stack_size()

    def get_closed_overs(self):
        return self._closed_overs

    def get_base_code(self):
        return self._code.get_base_code()

    def get_debug_points(self):
        return self._code.get_debug_points()

class Undefined(object.Object):
    _type = object.Type(u"Undefined")

    def type(self):
        return Undefined._type

undefined = Undefined()

class DynamicVars(py_object):
    def __init__(self):
        self._vars = [{}]

    def push_binding_frame(self):
        self._vars.append(self._vars[-1].copy())

    def pop_binding_frame(self):
        self._vars.pop()

    def get_var_value(self, var, not_found):
        return self._vars[-1].get(var, not_found)

    def set_var_value(self, var, val):
        self._vars[-1][var] = val

_dynamic_vars = DynamicVars()

class Var(BaseCode):
    _type = object.Type(u"Var")
    _immutable_fields_ = ["_rev?"]

    def type(self):
        return Var._type

    def __init__(self, ns, name):
        self._ns = ns
        self._name = name
        self._rev = 0
        self._root = undefined
        self._dynamic = False

    def set_root(self, o):
        self._rev += 1
        self._root = o
        return self

    def set_value(self, val):
        affirm(self._dynamic, u"Can't set the value of a non-dynamic var")
        _dynamic_vars.set_var_value(self, val)
        return self


    def set_dynamic(self):
        self._dynamic = True
        self._rev += 1

    def get_dynamic_value(self):
        return _dynamic_vars.get_var_value(self, self._root)

    @elidable_promote()
    def is_dynamic(self, rev):
        return self._dynamic

    @elidable_promote()
    def get_root(self, rev):
        return self._root


    def deref(self):
        if self.is_dynamic(self._rev):
            return self.get_dynamic_value()
        else:
            val = self.get_root(self._rev)
            affirm(val is not undefined, u"Var " + self._name + u" is undefined")
            return val

    def is_defined(self):
        return self._root is not undefined

    def _invoke(self, args):
        return self.deref().invoke(args)

class bindings(py_object):
    def __init__(self, *args):
       self._args = list(args)

    def __enter__(self):
        _dynamic_vars.push_binding_frame()
        for x in range(0, len(self._args), 2):
            self._args[x].set_value(self._args[x + 1])

    def __exit__(self, exc_type, exc_val, exc_tb):
        _dynamic_vars.pop_binding_frame()


class Refer(py_object):
    def __init__(self, ns, refer_syms=[], refer_all=False):
        self._namespace = ns
        self._refer_syms = refer_syms
        self._refer_all = refer_all


class Namespace(object.Object):
    _type = object.Type(u"pixie.stdlib.Namespace")

    def type(self):
        return Namespace._type

    def __init__(self, name):
        self._registry = {}
        self._name = name
        self._refers = {}

    def intern_or_make(self, name):
        assert name is not None
        affirm(isinstance(name, unicode), u"Var names must be unicode")
        v = self._registry.get(name, None)
        if v is None:
            v = Var(self._name, name)
            self._registry[name] = v
        return v

    def add_refer(self, ns, as_nm=None, refer_all=False):
        assert isinstance(ns, Namespace)
        if as_nm is not None:
            assert isinstance(as_nm, unicode)

        if as_nm is None:
            as_nm = ns._name

        self._refers[as_nm] = Refer(ns, refer_all=refer_all)

    def include_stdlib(self):
        stdlib = _ns_registry.find_or_make(u"pixie.stdlib")
        self.add_refer(stdlib, refer_all=True)

    def resolve(self, s, use_refers=True):
        import pixie.vm.symbol as symbol
        affirm(isinstance(s, symbol.Symbol), u"Must resolve symbols")
        ns = rt.namespace(s)
        name = rt.name(s)

        if ns is not None:
            refer = self._refers.get(ns, None)
            resolved_ns = None
            if refer is not None:
                resolved_ns = refer._namespace
            if resolved_ns is None:
                resolved_ns = _ns_registry.get(ns, None)
            if resolved_ns is None:
                affirm(False, u"Unable to resolve namespace: " + ns + u" inside namespace " + self._name)
        else:
            resolved_ns = self

        var = resolved_ns._registry.get(name, None)
        if var is None and use_refers:
            for refer_nm in self._refers:
                refer = self._refers[refer_nm]
                if name in refer._refer_syms or refer._refer_all:
                    var = refer._namespace.resolve(symbol.Symbol(name), False)
                if var is not None:
                    return var
            return None
        return var



    def get(self, name, default):
        return self._registry.get(name, default)

class NamespaceRegistry(py_object):
    def __init__(self):
        self._registry = {}

    def find_or_make(self, name):
        #affirm(isinstance(name, unicode), u"Namespace names must be unicode")
        v = self._registry.get(name, None)
        if v is None:
            v = Namespace(name)
            self._registry[name] = v
        return v


    def get(self, name, default):
        return self._registry.get(name, default)

_ns_registry = NamespaceRegistry()


def intern_var(ns, name=None):
    if name is None:
        name = ns
        ns = u""

    return _ns_registry.find_or_make(ns).intern_or_make(name)

def get_var_if_defined(ns, name, els=None):
    w_ns = _ns_registry.get(ns, None)
    if w_ns is None:
        return els
    return w_ns.get(name, els)



class DefaultProtocolFn(NativeFn):
    def __init__(self, pfn):
        self._pfn = pfn

    def _invoke(self, args):
        from pixie.vm.string import String
        tp = args[0].type()._name
        affirm(False, u"No override for " + tp + u" on " + self._pfn._name + u" in protocol " + self._pfn._protocol._name)


class Protocol(object.Object):
    _type = object.Type(u"Protocol")

    __immutable_fields__ = ["_rev?"]
    def type(self):
        return Protocol._type

    def __init__(self, name):
        self._name = name
        self._polyfns = {}
        self._satisfies = {}
        self._rev = 0


    def add_method(self, pfn):
        self._polyfns[pfn] = pfn

    def add_satisfies(self, tp):
        self._satisfies[tp] = tp
        self._rev += 1

    @elidable_promote()
    def _get_satisfies(self, tp, rev):
        return tp in self._satisfies

    def satisfies(self, tp):
        return self._get_satisfies(tp, self._rev)


class PolymorphicFn(BaseCode):
    _type = object.Type(u"PolymorphicFn")
    def type(self):
        return PolymorphicFn._type

    __immutable_fields__ = ["_rev?"]
    def __init__(self, name, protocol):
        BaseCode.__init__(self)
        self._name = name
        self._dict = {}
        self._rev = 0
        self._protocol = protocol
        self._default_fn = DefaultProtocolFn(self)
        protocol.add_method(self)

    def extend(self, tp, fn):
        self._dict[tp] = fn
        self._rev += 1
        self._protocol.add_satisfies(tp)

    def set_default_fn(self, fn):
        self._default_fn = fn
        self._rev += 1

    @elidable_promote()
    def get_protocol_fn(self, tp, rev):
        fn = self._dict.get(tp, self._default_fn)
        return promote(fn)

    def _invoke(self, args):
        a = args[0].type()
        fn = self.get_protocol_fn(a, self._rev)
        try:
            return fn.invoke(args)
        except object.WrappedException as ex:
            ex._ex._trace.append(object.PolymorphicCodeInfo(self._name, args[0].type()))
            raise

class DoublePolymorphicFn(BaseCode):
    """A function that is polymorphic on the first two arguments"""
    _type = object.Type(u"DoublePolymorphicFn")

    def type(self):
        return DefaultProtocolFn._type

    __immutable_fields__ = ["_rev?"]
    def __init__(self, name, protocol):
        BaseCode.__init__(self)
        self._name = name
        self._dict = {}
        self._rev = 0
        self._protocol = protocol
        self._default_fn = DefaultProtocolFn(self)
        protocol.add_method(self)

    def extend2(self, tp1, tp2, fn):
        d1 = self._dict.get(tp1, None)
        if d1 is None:
            d1 = {}
            self._dict[tp1] = d1
        d1[tp2] = fn
        self._rev += 1
        self._protocol.add_satisfies(tp1)

    def set_default_fn(self, fn):
        self._default_fn = fn
        self._rev += 1

    @elidable_promote()
    def get_fn(self, tp1, tp2, _rev):
        d1 = self._dict.get(tp1, None)
        if d1 is None:
            return self._default_fn
        fn = d1.get(tp2, self._default_fn)
        return promote(fn)

    def _invoke(self, args):
        affirm(len(args) >= 2, u"DoublePolymorphicFunctions take at least two args")
        a = args[0].type()
        b = args[1].type()
        fn = self.get_fn(a, b, self._rev)
        return fn.invoke(args)

def munge(s):
    return s.replace("-", "_").replace("?", "_QMARK_").replace("!", "_BANG_")

import inspect
def defprotocol(ns, name, methods):
    """Define a protocol in the given namespace with the given name and methods, vars will
       be created in the namespace for the protocol and methods. This function will dump
       variables for the created protocols/methods in the globals() where this function is called."""
    ns = unicode(ns)
    name = unicode(name)
    methods = map(unicode, methods)
    gbls = inspect.currentframe().f_back.f_globals
    proto =  Protocol(name)
    intern_var(ns, name).set_root(proto)
    gbls[munge(name)] = proto
    for method in methods:
        poly = PolymorphicFn(method,  proto)
        intern_var(ns, method).set_root(poly)
        gbls[munge(method)] = poly

def assert_type(x, tp):
    affirm(isinstance(x, tp), u"Fatal Error, this should never happen")
    return x


## PYTHON FLAGS
CO_VARARGS = 0x4
def wrap_fn(fn, tp=object.Object):
    """Converts a native Python function into a pixie function."""
    def as_native_fn(f):
        return type("W"+fn.__name__, (NativeFn,), {"inner_invoke": f})()

    def as_variadic_fn(f):
        return type("W"+fn.__name__[:len("__args")], (NativeFn,), {"inner_invoke": f})()

    code = fn.func_code
    if fn.__name__.endswith("__args"):
        return as_variadic_fn(lambda self, args: fn(args))

    fn_name = unicode(getattr(fn, "__real_name__", fn.__name__))

    if code.co_flags & CO_VARARGS:
        raise Exception("Variadic functions not supported by wrap")
    else:
        argc = code.co_argcount
        if argc == 0:
            def wrapped_fn(self, args):
                affirm(len(args) == 0, u"Expected 0 arguments to " + fn_name)
                try:
                    return fn()
                except object.WrappedException as ex:
                    ex._ex._trace.append(object.NativeCodeInfo(fn_name))
                    raise
            return as_native_fn(wrapped_fn)

        if argc == 1:
            def wrapped_fn(self, args):
                affirm(len(args) == 1, u"Expected 1 arguments to " + fn_name)
                try:
                    return fn(args[0])
                except object.WrappedException as ex:
                    ex._ex._trace.append(object.NativeCodeInfo(fn_name))
                    raise
            return as_native_fn(wrapped_fn)

        if argc == 2:
            def wrapped_fn(self, args):
                affirm(len(args) == 2, u"Expected 2 arguments to " + fn_name)
                try:
                    return fn(args[0], args[1])
                except object.WrappedException as ex:
                    ex._ex._trace.append(object.NativeCodeInfo(fn_name))
                    raise
            return as_native_fn(wrapped_fn)
        if argc == 3:
            def wrapped_fn(self, args):
                affirm(len(args) == 3, u"Expected 3 arguments to " + fn_name)

                try:
                    return fn(args[0], args[1], args[2])
                except object.WrappedException as ex:
                    ex._ex._trace.append(object.NativeCodeInfo(fn_name))
                    raise
            return as_native_fn(wrapped_fn)


def extend(pfn, tp1, tp2=None):
    """Extends a protocol to the given Type (not python type), with the decorated function
       wraps the decorated function"""
    if isinstance(tp1, type):
        assert_tp = tp1
        tp1 = tp1._type
    else:
        assert_tp = object.Object

    def extend_inner(fn):
        if tp2 is None:
            pfn.extend(tp1, wrap_fn(fn, assert_tp))
        else:
            pfn.extend2(tp1, tp2, wrap_fn(fn, assert_tp))

        return pfn

    return extend_inner



def as_var(ns, name=None):
    """Locates a var with the given name (defaulting to the namespace pixie.stdlib), sets
       the root to the decorated function. If the function is not an instance of BaseCode it will
       be wrapped. """
    if name is None:
        name = ns
        ns = "pixie.stdlib"

    name = name if isinstance(name, unicode) else unicode(name)
    ns = ns if isinstance(ns, unicode) else unicode(ns)

    var = intern_var(ns, name)
    def with_fn(fn):
        fn.__real_name__ = name
        if not isinstance(fn, object.Object):
            fn = wrap_fn(fn)
        var.set_root(fn)
        return fn
    return with_fn


def returns(type):
    """Tags a var as for unwrapping in rt. When rt imports this var it will be automatically converted to this type"""
    def with_fn(fn):
        fn._returns = type
        print "registered", fn
        return fn
    return with_fn
