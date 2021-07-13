# Copyright (c) 2008 Kirei AB. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
# GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
# IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN
# IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
######################################################################

require 5.008;
use warnings;
use strict;

use Getopt::Long;
use Pod::Usage;
use XML::LibXML;
use Net::IP;

######################################################################

my $debug = 0;

sub main {
    my $help    = 0;
    my $name    = undef;
    my $format  = "bind";
    my $zonedir = "";

    GetOptions(
        'help|?'    => \$help,
        'debug+'    => \$debug,
        'format=s'  => \$format,
        'zonedir=s' => \$zonedir,
    ) or pod2usage(2);
    pod2usage(1) if ($help);

    my $input = shift @ARGV;

    pod2usage(2) unless ($input);

    unless ($format eq "bind" or $format eq "nsd") {
        die "unknown format";
    }

    my $parser = XML::LibXML->new();
    my $doc    = $parser->parse_file($input);

    die unless ($doc);

    my $root = $doc->getDocumentElement;

    validate($root);

    foreach my $p ($root->getElementsByTagName('peer')) {
        generate_bind($p, $zonedir) if ($format eq "bind");
        generate_nsd($p, $zonedir) if ($format eq "nsd");
    }
}

sub validate {
    my $root = shift;

    foreach my $m ($root->getElementsByTagName('primary')) {
        my $tsig = $m->findvalue('@tsig');
        my $addr = $m->textContent;

        if ($tsig) {
            die "syntax error: $tsig" unless ($tsig =~ /^[a-z0-9\.\-_]+$/);
        }
        die "syntax error: $addr" unless (new Net::IP($addr));
    }

    foreach my $z ($root->getElementsByTagName('zone')) {
        my $zone = lc($z->textContent);

        die "syntax error: $zone" unless ($zone =~ /^[a-z0-9\.-]+$/);
    }
}

sub generate_bind {
    my $root    = shift;
    my $zonedir = shift;

    my @servers = ();
    my @notify  = ();

    foreach my $m ($root->getElementsByTagName('primary')) {
        my $tsig = $m->findvalue('@tsig');
        my $addr = $m->textContent;

        if ($tsig) {
            push @servers, sprintf("%s key %s", $addr, $tsig);
        } else {
            push @servers, sprintf("%s", $addr);
        }
        push @notify, sprintf("%s", $addr);
    }

    foreach my $z ($root->getElementsByTagName('zone')) {
        my $zone = lc($z->textContent);

        printf("zone \"%s\" {\n", $zone);
        printf("  type slave;\n");
        printf("  file \"%s%s\";\n", $zonedir, zone2file($zone));
        printf("  masters { %s; };\n",      join("; ", @servers));
        printf("  allow-notify { %s; };\n", join("; ", @notify));
        printf("  allow-transfer { none; };\n");
        printf("};\n");
        printf("\n");
    }
}

sub generate_nsd {
    my $root    = shift;
    my $zonedir = shift;

    my @servers = ();
    my @notify  = ();

    foreach my $m ($root->getElementsByTagName('primary')) {
        my $tsig = $m->findvalue('@tsig');
        my $addr = $m->textContent;

        if ($tsig) {
            push @servers, sprintf("%s %s", $addr, $tsig);
        } else {
            push @servers, sprintf("%s NOKEY", $addr);
        }
        push @notify, sprintf("%s NOKEY", $addr);
    }

    foreach my $z ($root->getElementsByTagName('zone')) {
        my $zone = $z->textContent;

        printf("zone:\n");
        printf("  name: \"%s\"\n", $zone);
        printf("  zonefile: \"%s%s\"\n", $zonedir, zone2file($zone));
        foreach my $x (@notify) {
            printf("  allow-notify: %s\n", $x);
        }
        foreach my $x (@servers) {
            printf("  request-xfr: %s\n", $x);
        }
        printf("\n");
    }
}

sub zone2file {
    my $zonename = shift;

    $zonename =~ s,/,-,g;
    return lc($zonename);
}

main();

__END__

=head1 NAME

dper - DNS Peering Configuration Generator

=head1 SYNOPSIS

dper [options] filename

Options:

 --help           brief help message
 --debug          enable debugging
 --format=xxx     select output format (bind, nsd, ...)
 --zonedir=dir    zone file directory

