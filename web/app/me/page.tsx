import type { Metadata } from "next";
import { Account } from "@/components/Account";

export const metadata: Metadata = {
  title: "Your calls · Landline",
  description: "The profile and calls Landline saved for your number.",
};

export default function MePage() {
  return <Account />;
}
