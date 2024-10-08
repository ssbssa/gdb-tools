import gdb
import sys
import re

try: a=xrange # Python 3 compatibility
except:
    def xrange(f,t,s=1): return range(int(f),int(t),s)

try:
    maxint = sys.maxint
except:
    maxint = sys.maxsize

aliases = dict()
scopes = list()

try:
    gdb.parse_and_eval("$_strlen")("")
    can_call_conv_func = True
except:
    can_call_conv_func = False

def val2str(v):
    try: v = v.referenced_value() if v.type.code==gdb.TYPE_CODE_REF else v
    except: pass
    return str(v)

# this uses gdb convenience variables to avoid convering all arguments to strings
def parse_and_call(func, *args):
    s = func + '('
    for i, a in enumerate(args):
        n = 'duel_eval_func_call_'+str(i)
        gdb.set_convenience_variable(n, a)
        s += '$' + n + ','
    return gdb.parse_and_eval(s[0:-1]+')')

class Expr(object):
    def name(self): return self.name_
    def value(self): return self.value_
    def eval(self): yield self.name(), self.value()
    def no_parens(self): return False
    def scoped_eval(self, v):
        g = self.eval()
        while True:
            scopes.append(v)
            try:
                p = next(g)
            except StopIteration:
                return
            finally:
                scopes.pop()
            yield p

class Literal(Expr):
    def __init__(self, n, v): self.name_, self.value_ = n, v
    def no_parens(self): return True

class MethodCaller(object):
    def __init__(self, c, n):
        self.class_, self.name_ = c, n
    def find_xmethod(self):
        progspace = gdb.current_progspace()
        t = self.class_.type.strip_typedefs()
        for objfile in progspace.objfiles():
            for xmethod in objfile.xmethods:
                if xmethod.enabled:
                    xm = xmethod.match(t, self.name_)
                    if xm:
                        return xm
        for xmethod in progspace.xmethods:
            if xmethod.enabled:
                xm = xmethod.match(t, self.name_)
                if xm:
                    return xm
        for xmethod in gdb.xmethods:
            if xmethod.enabled:
                xm = xmethod.match(t, self.name_)
                if xm:
                    return xm
    def __call__(self, *args):
        xm = self.find_xmethod()
        if xm:
            return xm(self.class_.address, *args)
        return self.class_[self.name_](self.class_.address, *args)
    def __str__(self):
        return "<MethodCaller for %s::%s>" % (self.class_.type.strip_typedefs().tag, self.name_)

def sizeof(v):
    return gdb.Value(v.type.sizeof)

template_re = None

def filter_templates(n):
    if not n: return None
    if '<' not in n:
        return n
    global template_re
    if template_re is None:
        template_re = re.compile(r"(?<!\boperator)((?<!\boperator<)<|(?<!\boperator>)(?<!\boperator<=)>)")
    level = 0
    rest_arr = []
    for s in template_re.split(n):
        if s == "<":
            level += 1
        elif s == ">":
            if level > 0:
                level -= 1
        elif level == 0:
            rest_arr.append(s)
    return "".join(rest_arr)

word_re = None

def function_name(n):
    if not n: return None
    n = filter_templates(n)
    global word_re
    if word_re is None:
        word_re = re.compile(r"\w+")
    words = word_re.findall(n)
    if words: return words[-1]

class Frame(object):
    def __init__(self, f):
        self.frame = f
    def __getitem__(self, key):
        block = self.frame.block()
        while block:
            if not block.is_global:
                for symbol in block:
                    if symbol.is_argument or symbol.is_variable or symbol.is_constant:
                        if symbol.name == key:
                            return symbol.value(self.frame)
            if block.function:
                break
            block = block.superblock
        raise gdb.error("Variable '%s' not found in frame" % key)
    def __eq__(self, other):
        return function_name(self.frame.name()) == function_name(other.frame.name())
    def __str__(self):
        return "<Frame %d: %s>" % (self.frame.level(), filter_templates(self.frame.name()))

def find_frame(n):
    try:
        f = gdb.newest_frame()
        while f:
            if n == function_name(f.name()): return Frame(f)
            f = f.older()
    except gdb.error:
        pass

def count_frames():
    c = 0
    try:
        f = gdb.newest_frame()
        while f:
            c += 1
            f = f.older()
    except gdb.error:
        pass
    return gdb.Value(c)

def get_frame(idx):
    idx = int(idx)
    c = 0
    f = gdb.newest_frame()
    while f:
        if c == idx:
            return Frame(f)
        c += 1
        f = f.older()
    raise IndexError("Frame '%d' does not exist" % idx)

class Ident(Expr):
    def __init__(self, n): self.name_, self.scope, self.sym, self.method, self.frame = n, None, None, False, None
    def no_parens(self): return True
    def symval(self, s): return s.value(gdb.selected_frame()) if s.needs_frame else s.value()
    def value(self):
        if self.scope:
            if self.method:
                c = scopes[self.scope]
                if c.type.code == gdb.TYPE_CODE_REF or c.type.code == gdb.TYPE_CODE_RVALUE_REF:
                    c = c.referenced_value()
                if c.type.code == gdb.TYPE_CODE_PTR:
                    c = c.dereference()
                return MethodCaller(c, self.name_)
            return scopes[self.scope][self.name_]
        if self.sym: return self.symval(self.sym)
        if self.frame: return self.frame
        if self.name_ in aliases: return aliases[self.name_][1]
        for self.scope in range(len(scopes)-1,-1,-1):
            try:
                c = scopes[self.scope]
                if c.type.code == gdb.TYPE_CODE_REF or c.type.code == gdb.TYPE_CODE_RVALUE_REF:
                    c = c.referenced_value()
                if c.type.code == gdb.TYPE_CODE_PTR:
                    c = c.dereference()
                # this throws if no method is found
                c.type.method(self.name_)
                self.method = True
                return MethodCaller(c, self.name_)
            except:
                pass

            try: return scopes[self.scope][self.name_]
            except gdb.error: self.scope = None
        try: self.sym = gdb.lookup_symbol(self.name_)[0]
        except gdb.error: self.sym = gdb.lookup_global_symbol(self.name_)
        if self.sym:
            if self.sym.type.code != gdb.TYPE_CODE_FUNC:
                return self.symval(self.sym)
            self.sym = None
        self.frame = find_frame(self.name_)
        if self.frame: return self.frame
        if self.name_ == "sizeof": return sizeof
        elif self.name_ == "frames_no": return count_frames()
        elif self.name_ == "frame": return get_frame
        return gdb.parse_and_eval(self.name_)

class Underscore(Expr):
    def __init__(self, n): self.name_ = n
    def eval(self):
        v = scopes[-len(self.name_)]
        yield val2str(v), v

class UnaryBase(Expr):
    def __init__(self, a): self.arg1_ = a
    def name(self): return self.name_.format(self.arg1_.name())
    def eval(self):
        for n,v in self.arg1_.eval():
            yield self.name_.format(n), self.value(v)

class Unary(UnaryBase):
    def __init__(self, n, a, v):
        super (Unary, self).__init__ (a)
        self.name_ = n if '{' in n else n + '{0}'
        self.value = v

class UnaryPostfix(UnaryBase):
    def __init__(self, a, n, v):
        super (UnaryPostfix, self).__init__ (a)
        self.name_ = n if '{' in n else '{0}' + n
        self.value = v

class Parens(UnaryBase):
    name_ = "({0})"
    def no_parens(self): return True
    def eval(self):
        for n,v in self.arg1_.eval():
            yield  n if self.arg1_.no_parens() else '('+n+')', v

class Curlies(UnaryBase):
    name_ = "({0})"
    def eval(self):
        for n,v in self.arg1_.eval():
            yield val2str(v), v

class BinaryBase(Expr):
    def __init__(self, a1, a2): self.arg1_, self.arg2_ = a1, a2
    def name(self): return self.name_.format(self.arg1_.name(), self.arg2_.name())
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            for n2,v2 in self.arg2_.eval():
                yield self.name_.format(n1, n2), self.value(v1, v2)

class Binary(BinaryBase):
    def __init__(self, a1, n, a2, v):
        super (Binary, self).__init__ (a1, a2)
        self.name_ = n if '{' in n else '{0} ' + n + ' {1}'
        self.value = v

class Filter(Binary):
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            for n2,v2 in self.arg2_.eval():
                if self.value(v1, v2):
                    yield self.name_.format(n1, n2), v1

class Struct(BinaryBase):
    def __init__(self, a1, n, a2):
        super (Struct, self).__init__ (a1, a2)
        self.name_ = n
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            for n2,v2 in self.arg2_.scoped_eval(v1):
                yield self.name_.format(n1, n2), v2

class StructWalk(BinaryBase):
    name_ = '{0}-->{1}'
    def path2str(self, path):
        if len(path) == 1: return path[0]
        s, prev, cnt = path[0], path[1], 1
        for m in path[2:] + [None]:
            if m == prev: cnt += 1
            else:
                if cnt == 1: s += '->{0}'.format(prev)
                else: s += '-->{0}[[{1}]]'.format(prev, cnt)
                prev, cnt = m, 1
        return s
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            queue = [ ([n1], v1) ]
            while queue:
                n1, v1 = queue.pop()
                if not v1: continue
                yield self.path2str(n1), v1
                l = len(queue)
                for n2,v2 in self.arg2_.scoped_eval(v1.dereference()):
                    queue.insert(l, (n1+[n2], v2))

class TakeNth(BinaryBase):
    name_ = '{0}[[{1}]]'
    def eval(self):
        l = None
        val = None
        prev_idx = -1
        for n2,v2 in self.arg2_.eval():
            v2 = int(v2)
            if v2 < 0:
                if l is None: l = sum(1 for i in self.arg1_.eval())
                v2 += l
                if isinstance(self.arg2_, Curlies): n2 = str(v2)
                if v2 < 0: raise StopIteration
            if val is None or v2 <= prev_idx:
                val = self.arg1_.eval()
                prev_idx = -1
            for i in xrange(0, v2 - prev_idx - 1): next(val)
            prev_idx = v2
            n1, v1 = next(val)
            yield self.name_.format(self.arg1_.name(), n2), v1

class Until(BinaryBase):
    name_ = '{0}@{1}'
    def eval(self):
        if isinstance(self.arg2_, Literal): f = lambda x,y: x == y
        else: f= lambda x,y: y
        for n1,v1 in self.arg1_.eval():
            stop, output = False, False
            for n2,v2 in self.arg2_.scoped_eval(v1):
                if f(v1, v2): stop = True
                else: output = True
                if stop and output: break
            if output: yield n1, v1
            if stop: break

class URange(UnaryBase):
    def __init__(self, n, a1, to):
        super (URange, self).__init__ (a1)
        self.name_, self.to = n, to
    def no_parens(self): return True
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            for i in xrange(0 if self.to else v1, v1 if self.to else maxint):
                v = gdb.Value(i).cast(v1.type)
                yield val2str(v), v

class BiRange(BinaryBase):
    name_ = '{0}..{1}'
    def no_parens(self): return True
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            for n2,v2 in self.arg2_.eval():
                step = 1 if v1 < v2 else -1
                for i in xrange(v1, v2 + step, step):
                    v = gdb.Value(i).cast(v1.type)
                    yield val2str(v), v

class EagerGrouping(UnaryBase):
    def __init__(self, n, a, v):
        super (EagerGrouping, self).__init__ (a)
        self.name_, self.add = n + '{0}', v
    def eval(self):
        i = 0
        for n,v in self.arg1_.eval(): i =  self.add(i, v)
        yield self.name(), gdb.Value(i)

class LazyGrouping(UnaryBase):
    def __init__(self, n, a, v0, v):
        super (LazyGrouping, self).__init__ (a)
        self.name_, self.init_val, self.add = n + '{0}', v0, v
    def eval(self):
        i = self.init_val
        for n,v in self.arg1_.eval():
            i = self.add(i, v)
            if i != self.init_val: break
        yield self.name(), gdb.Value(i)

class Ternary(Expr):
    def __init__(self, n, a1, a2, a3):
        self.name_, self.arg1_, self.arg2_, self.arg3_= n, a1, a2, a3
    def name(self):
        return self.name_.format(self.arg1_.name(), self.arg2_.name(),
                                 self.arg3_ and self.arg3_.name())
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            for n2,v2 in self.arg2_.eval():
                if self.arg3_:
                    for n3,v3 in self.arg3_.eval():
                        yield self.name_.format(n1, n2, n3), v2 if v1 else v3
                else:
                    if v1: yield self.name_.format(n1, n2), v2

class Alias(BinaryBase):
    name_ = '{0} := {1}'
    def no_parens(self): return True
    def eval(self):
        for n2,v2 in self.arg2_.eval():
            try: v2 = v2.reference_value()
            except: pass
            aliases[self.arg1_.name()] = (n2, v2)
            yield self.arg1_.name(), v2

class Enumerate(BinaryBase):
    name_ = '{0}#{1}'
    def eval(self):
        for i, nv1 in enumerate(self.arg1_.eval()):
            aliases[self.arg2_.name()] = (str(i), i)
            yield nv1

class List(Expr):
    def __init__(self, args): self.args_ = args
    def name(self): return ','.join([e.name() for e in self.args_])
    def no_parens(self): return self.cur.no_parens()
    def eval(self):
        for self.cur in self.args_:
            for n2,v2 in self.cur.eval():
                yield n2, v2

class Statement(Expr):
    def __init__(self, args): self.args_ = args
    def name(self): return '; '.join([e.name() for e in self.args_])
    def eval(self):
        for v in self.args_[:-1]:
            for n2,v2 in v.eval(): pass
        for n2,v2 in self.args_[-1].eval():
            yield n2, v2

class Foreach(BinaryBase):
    name_ = '{0} => {1}'
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            for n2,v2 in self.arg2_.scoped_eval(v1):
                yield n2, v2

class Call(BinaryBase):
    name_ = '{0}({1})'
    def eval(self):
        for n1,v1 in self.arg1_.eval():
            args = self.arg2_.args_
            gens = [] + args
            nams = [] + args
            vals = [] + args
            cur = -1
            if not can_call_conv_func and v1.type.code == gdb.TYPE_CODE_INTERNAL_FUNCTION:
                v1=lambda *args: parse_and_call(n1, *args)
            while True:
                while cur < len(args)-1:
                    cur += 1
                    gens[cur] = args[cur].eval()
                    nams[cur], vals[cur] = next(gens[cur])
                yield self.name_.format(n1, ','.join(nams)), v1(*vals)
                repeat = True
                while repeat and cur >= 0:
                    repeat = False
                    try: nams[cur], vals[cur] = next(gens[cur])
                    except StopIteration:
                        cur -= 1
                        repeat = True
                if cur < 0: break
