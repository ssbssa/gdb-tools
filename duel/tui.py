import gdb
from gdb.command.tui_windows import VariableWindow, VarNameValue
from duel import parser, expr
from arpeggio import visit_parse_tree

class DuelPrinter (object):
    def __init__ (self, expr_tree):
        self.expr_tree = expr_tree

    def children (self):
        for name, val in self.expr_tree.eval():
            val = expr.val2str(val)
            yield name, val

duel_list = []

class DuelWindow (VariableWindow):
    def __init__ (self, win):
        super(DuelWindow, self).__init__(win, "dlv")

    def variables (self):
        for i, duel_cmd in enumerate(duel_list, 1):
            if duel_cmd is not None:
                (arg, val) = duel_cmd
                yield VarNameValue(arg, val, num=i, exp=True)

class DuelAdd (gdb.Command):
    """Add expression to duel window."""

    def __init__ (self):
        super (DuelAdd, self).__init__ ("dla", gdb.COMMAND_DATA, gdb.COMPLETE_EXPRESSION)

    def invoke (self, arg, from_tty):
        parse_tree = parser.parser.parse(arg)
        expr.scopes = list()
        expr_tree = visit_parse_tree(parse_tree, parser.DuelVisitor(debug=False))
        duel_list.append((arg, DuelPrinter(expr_tree)))

class DuelDel (gdb.Command):
    """Remove expression from duel window."""

    def __init__ (self):
        super (DuelDel, self).__init__ ("dld", gdb.COMMAND_DATA)

    def invoke (self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        if len(argv) != 1 or not argv[0].isdigit():
            raise gdb.GdbError("This expects the number of a duel expression.")
        num = int(argv[0])
        if num < 1 or num > len(duel_list) or duel_list[num-1] is None:
            raise gdb.GdbError("No duel expression number %d." % num)
        duel_list[num-1] = None
